#include "dji_edge_transport/transport_node.hpp"
#include "dji_edge_transport/frame_context_builder.hpp"

#include <diagnostic_msgs/msg/diagnostic_status.hpp>
#include <rclcpp/qos.hpp>

#include <algorithm>
#include <chrono>
#include <nlohmann/json.hpp>
#include <utility>

namespace dji_edge_transport {
namespace {
std::uint64_t now_ns()
{
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count());
}

double age_seconds(std::uint64_t current, std::uint64_t observed)
{
  if (observed == 0 || current < observed) return -1.0;
  return static_cast<double>(current - observed) / 1e9;
}
}

TransportNode::TransportNode()
: Node("dji_edge_transport")
{
  bind_host_ = declare_parameter<std::string>("bind_host", "0.0.0.0");
  max_json_bytes_ = declare_parameter<int>("max_json_bytes", 1200);
  const auto telemetry_port = declare_parameter<int>("telemetry_port", 5500);
  const auto metadata_port = declare_parameter<int>("frame_metadata_port", 5501);
  const auto clock_port = declare_parameter<int>("clock_port", 5502);
  const auto android_clock_host = declare_parameter<std::string>("android_clock_host", "");
  const auto clock_interval_s = declare_parameter<double>("clock_ping_interval_s", 1.0);
  const auto clock_timeout_s = declare_parameter<double>("clock_response_timeout_s", 0.5);
  const auto primary_port = declare_parameter<int>("primary_rtp_port", 5600);
  const auto payload_type = declare_parameter<int>("rtp_payload_type", 96);
  const auto latency_ms = declare_parameter<int>("rtp_latency_ms", 20);
  primary_topic_ = declare_parameter<std::string>("primary_topic", "/dji/primary/image_raw");
  context_topic_ = declare_parameter<std::string>("primary_context_topic", "/dji/primary/frame_context");
  navigation_topic_ = declare_parameter<std::string>("navigation_topic", "/dji/navigation/state");
  status_topic_ = declare_parameter<std::string>("status_topic", "/dji/edge/transport_status");
  diagnostics_topic_ = declare_parameter<std::string>("diagnostics_topic", "/dji/diagnostics");
  const auto diagnostics_period = declare_parameter<double>("diagnostics_period_s", 1.0);
  const auto dashboard_port = declare_parameter<int>("dashboard_port", 8090);
  const auto dashboard_local_config = declare_parameter<std::string>(
    "dashboard_local_config", "/workspace/transport.local.yaml");

  image_publisher_ = create_publisher<sensor_msgs::msg::Image>(primary_topic_, rclcpp::SensorDataQoS());
  context_publisher_ = create_publisher<dji_edge_transport::msg::FrameContext>(context_topic_, rclcpp::SensorDataQoS());
  navigation_publisher_ = create_publisher<dji_edge_transport::msg::NavigationState>(navigation_topic_, 10);
  status_publisher_ = create_publisher<msg::TransportStatus>(status_topic_, 10);
  diagnostics_publisher_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(diagnostics_topic_, 10);

  video_ = std::make_unique<PrimaryVideoPipeline>(
    bind_host_, static_cast<std::uint16_t>(primary_port), payload_type, latency_ms, handoff_,
    [this](const std::string & error) { RCLCPP_ERROR(get_logger(), "Primary pipeline: %s", error.c_str()); });
  video_->start();

  telemetry_receiver_ = std::make_unique<UdpReceiver>(
    bind_host_, static_cast<std::uint16_t>(telemetry_port), max_json_bytes_,
    [this](const auto & bytes, auto receive_ns) { on_telemetry(bytes, receive_ns); });
  metadata_receiver_ = std::make_unique<UdpReceiver>(
    bind_host_, static_cast<std::uint16_t>(metadata_port), max_json_bytes_,
    [this](const auto & bytes, auto receive_ns) { on_frame_metadata(bytes, receive_ns); });
  clock_receiver_ = std::make_unique<UdpReceiver>(
    bind_host_, static_cast<std::uint16_t>(clock_port), max_json_bytes_,
    [this](const auto & bytes, auto receive_ns) { on_clock(bytes, receive_ns); });
  telemetry_receiver_->start();
  metadata_receiver_->start();
  clock_receiver_->start();
  clock_pinger_ = std::make_unique<ClockPinger>(android_clock_host,
    static_cast<std::uint16_t>(clock_port), "edge-clock", clock_interval_s, clock_timeout_s,
    max_json_bytes_, [this](const ClockPacket & pong, std::uint64_t receive_ns) {
      if (!clock_mapper_.add_exchange(pong.t0_edge_send_mono_ns, pong.t1_android_rx_mono_ns,
          pong.t2_android_tx_mono_ns, receive_ns)) {
        RCLCPP_WARN(get_logger(), "Rejected invalid Android clock exchange");
      }
    });
  clock_pinger_->start();
  dashboard_ = std::make_unique<DashboardServer>(static_cast<std::uint16_t>(dashboard_port),
    dashboard_local_config, [this]() { return dashboard_state(); });
  if (!dashboard_->start()) {
    RCLCPP_WARN(get_logger(), "Dashboard unavailable: %s", dashboard_->error().c_str());
  }

  frame_timer_ = create_wall_timer(std::chrono::milliseconds(16), [this]() { publish_frame(); });
  status_timer_ = create_wall_timer(
    std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::duration<double>(diagnostics_period)),
    [this]() { publish_status(); });
}

TransportNode::~TransportNode()
{
  if (telemetry_receiver_) telemetry_receiver_->stop();
  if (metadata_receiver_) metadata_receiver_->stop();
  if (clock_receiver_) clock_receiver_->stop();
  if (clock_pinger_) clock_pinger_->stop();
  if (dashboard_) dashboard_->stop();
  if (video_) video_->close();
}

builtin_interfaces::msg::Time TransportNode::ros_stamp()
{
  const auto nanoseconds = get_clock()->now().nanoseconds();
  builtin_interfaces::msg::Time stamp;
  stamp.sec = static_cast<std::int32_t>(nanoseconds / 1000000000LL);
  stamp.nanosec = static_cast<std::uint32_t>(nanoseconds % 1000000000LL);
  return stamp;
}

void TransportNode::log_protocol_error(const std::string & category, const std::string & error)
{
  RCLCPP_DEBUG(get_logger(), "Rejected %s datagram: %s", category.c_str(), error.c_str());
}

void TransportNode::on_telemetry(const std::vector<std::uint8_t> & bytes, std::uint64_t)
{
  std::string error;
  auto packet = decode_telemetry(bytes, max_json_bytes_, error);
  if (!packet) {
    log_protocol_error("telemetry", error);
    return;
  }
  {
    std::lock_guard<std::mutex> lock(state_mutex_);
    latest_telemetry_ = *packet;
  }
  source_time_state_.add(*packet);
  if (const auto navigation = source_time_state_.latest()) {
    publish_navigation(*navigation);
  }
}

void TransportNode::on_frame_metadata(const std::vector<std::uint8_t> & bytes, std::uint64_t)
{
  std::string error;
  auto packet = decode_frame_metadata(bytes, max_json_bytes_, error);
  if (!packet) {
    log_protocol_error("frame metadata", error);
    return;
  }
  std::lock_guard<std::mutex> lock(state_mutex_);
  latest_metadata_ = *packet;
}

void TransportNode::on_clock(const std::vector<std::uint8_t> & bytes, std::uint64_t)
{
  std::string error;
  if (!decode_clock(bytes, max_json_bytes_, error)) log_protocol_error("clock", error);
}

void TransportNode::publish_navigation(const TelemetryPacket & packet)
{
  dji_edge_transport::msg::NavigationState message;
  message.header.stamp = ros_stamp();
  message.session = packet.session;
  message.android_mono_ns = packet.android_mono_ns;
  message.edge_receive_mono_ns = now_ns();
  message.transport_age_s = 0.0;
  message.latitude_deg = packet.latitude_deg;
  message.longitude_deg = packet.longitude_deg;
  message.altitude_m = packet.altitude_m;
  message.heading_deg = packet.heading_deg;
  message.position_valid = packet.position_valid;
  message.rtk_valid = packet.rtk_valid;
  message.position_source = !packet.position_valid ? dji_edge_transport::msg::NavigationState::POSITION_UNKNOWN :
    (packet.rtk_valid ? dji_edge_transport::msg::NavigationState::POSITION_RTK :
    dji_edge_transport::msg::NavigationState::POSITION_GPS_FALLBACK);
  message.gimbal_pitch_valid = packet.gimbal_pitch_valid;
  message.gimbal_pitch_deg = packet.gimbal_pitch_deg;
  message.gimbal_android_mono_ns = packet.gimbal_android_mono_ns;
  navigation_publisher_->publish(message);
}

void TransportNode::publish_frame()
{
  if (!video_) return;
  video_->poll_bus();
  auto frame = handoff_.take();
  if (!frame) return;
  sensor_msgs::msg::Image image;
  image.header.stamp = ros_stamp();
  image.header.frame_id = "dji_primary_camera";
  image.width = frame->width;
  image.height = frame->height;
  image.encoding = "bgr8";
  image.is_bigendian = false;
  image.step = frame->width * 3;
  image.data = std::move(frame->bgr);
  image_publisher_->publish(image);
  last_published_mono_ns_.store(now_ns());
  published_count_.fetch_add(1);

  const auto context = make_unavailable_primary_context(image.header, *frame);
  context_publisher_->publish(context);
}

void TransportNode::publish_status()
{
  if (!video_) return;
  video_->poll_bus();
  const auto snapshot = video_->snapshot();
  const auto current = now_ns();
  msg::TransportStatus status;
  status.header.stamp = ros_stamp();
  status.ingress_seen = snapshot.last_rtp_mono_ns != 0;
  status.ingress_age_s = age_seconds(current, snapshot.last_rtp_mono_ns);
  status.decoded_age_s = age_seconds(current, snapshot.last_decoded_mono_ns);
  status.published_age_s = age_seconds(current, last_published_mono_ns_.load());
  status.decoded_count = snapshot.decoded_count;
  status.published_count = published_count_.load();
  status.dropped_old_frame_count = handoff_.dropped_old_frames();
  status.width = snapshot.width;
  status.height = snapshot.height;
  status.decoder_backend = snapshot.decoder_backend;
  status.decoder_reason = snapshot.decoder_reason;
  status.pipeline_error = snapshot.pipeline_error;
  const auto clock = clock_mapper_.estimate();
  status.clock_ready = clock.ready;
  status.clock_sample_count = clock.sample_count;
  status.clock_best_rtt_ns = clock.best_rtt_ns;
  status.clock_offset_android_minus_edge_ns = clock.offset_android_minus_edge_ns;
  if (clock_pinger_) {
    status.clock_attempt_count = clock_pinger_->attempts();
    status.clock_success_count = clock_pinger_->successes();
    status.clock_timeout_count = clock_pinger_->timeouts();
    status.clock_error = clock_pinger_->last_error();
  }
  if (last_rate_mono_ns_ != 0 && current > last_rate_mono_ns_) {
    const auto seconds = static_cast<double>(current - last_rate_mono_ns_) / 1e9;
    status.decoded_fps = static_cast<double>(snapshot.decoded_count - last_rate_decoded_count_) / seconds;
    status.published_fps = static_cast<double>(published_count_.load() - last_rate_published_count_) / seconds;
  }
  last_rate_mono_ns_ = current;
  last_rate_decoded_count_ = snapshot.decoded_count;
  last_rate_published_count_ = published_count_.load();
  if (!snapshot.pipeline_error.empty()) {
    status.state = msg::TransportStatus::PIPELINE_ERROR;
    status.state_reason = snapshot.pipeline_error;
  } else if (!status.ingress_seen) {
    status.state = msg::TransportStatus::WAITING_FOR_UDP;
    status.state_reason = "no Primary RTP observed";
  } else if (snapshot.decoded_count == 0) {
    status.state = msg::TransportStatus::WAITING_FOR_KEYFRAME;
    status.state_reason = "RTP observed; waiting for a decodable keyframe";
  } else {
    status.state = published_count_.load() > 0 ? msg::TransportStatus::PUBLISHING : msg::TransportStatus::DECODING;
    status.state_reason = "Primary pipeline active";
  }
  status_publisher_->publish(status);

  diagnostic_msgs::msg::DiagnosticArray diagnostics;
  diagnostics.header = status.header;
  diagnostic_msgs::msg::DiagnosticStatus diagnostic;
  diagnostic.name = "dji_edge_transport/primary";
  diagnostic.hardware_id = "android-primary-rtp";
  diagnostic.level = status.state == msg::TransportStatus::PIPELINE_ERROR ?
    diagnostic_msgs::msg::DiagnosticStatus::ERROR : diagnostic_msgs::msg::DiagnosticStatus::OK;
  diagnostic.message = status.state_reason;
  diagnostics.status.push_back(diagnostic);
  diagnostics_publisher_->publish(diagnostics);
}

std::string TransportNode::dashboard_state() const
{
  const auto snapshot = video_ ? video_->snapshot() : VideoSnapshot{};
  const auto current = now_ns();
  const auto clock = clock_mapper_.estimate();
  nlohmann::json state{{"name", "DJI Transport Edge"}, {"primary", {
      {"ingress_age_s", age_seconds(current, snapshot.last_rtp_mono_ns)},
      {"decoded_age_s", age_seconds(current, snapshot.last_decoded_mono_ns)},
      {"published_age_s", age_seconds(current, last_published_mono_ns_.load())},
      {"decoded_count", snapshot.decoded_count}, {"published_count", published_count_.load()},
      {"dropped_old_frames", handoff_.dropped_old_frames()}, {"width", snapshot.width}, {"height", snapshot.height},
      {"decoder", snapshot.decoder_backend}, {"decoder_reason", snapshot.decoder_reason}, {"pipeline_error", snapshot.pipeline_error}}},
    {"clock", {{"ready", clock.ready}, {"sample_count", clock.sample_count}, {"best_rtt_ns", clock.best_rtt_ns},
      {"offset_android_minus_edge_ns", clock.offset_android_minus_edge_ns},
      {"attempts", clock_pinger_ ? clock_pinger_->attempts() : 0}, {"successes", clock_pinger_ ? clock_pinger_->successes() : 0},
      {"timeouts", clock_pinger_ ? clock_pinger_->timeouts() : 0},
      {"error", clock_pinger_ ? clock_pinger_->last_error() : ""}}}};
  return state.dump();
}

}  // namespace dji_edge_transport
