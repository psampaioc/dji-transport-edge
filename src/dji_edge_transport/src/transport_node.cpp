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

const char * primary_state_name(PrimaryHealthState state)
{
  switch (state) {
    case PrimaryHealthState::waiting_for_udp: return "waiting_for_udp";
    case PrimaryHealthState::waiting_for_keyframe: return "waiting_for_keyframe";
    case PrimaryHealthState::decoding: return "waiting_for_idr";
    case PrimaryHealthState::publishing: return "publishing";
    case PrimaryHealthState::pipeline_error: return "pipeline_error";
    case PrimaryHealthState::delivery_stalled: return "ros_delivery_stalled";
  }
  return "unknown";
}

const char * ingress_state(const VideoSnapshot & snapshot, double ingress_age_s, double stale_after_s)
{
  if (snapshot.last_rtp_mono_ns == 0) return "not_receiving";
  return ingress_age_s <= stale_after_s ? "receiving" : "stale";
}

const char * decoder_state(
  const VideoSnapshot & snapshot, double ingress_age_s, double decoded_age_s, double stale_after_s)
{
  if (!snapshot.pipeline_error.empty()) return "error";
  if (snapshot.last_decoded_mono_ns == 0) return "waiting_for_keyframe";
  if (decoded_age_s <= stale_after_s) return "decoding";
  return ingress_age_s <= stale_after_s ? "waiting_for_idr" : "no_recent_input";
}

const char * ros_delivery_state(double published_age_s, double stale_after_s)
{
  if (published_age_s < 0.0) return "not_publishing";
  return published_age_s <= stale_after_s ? "publishing" : "stale";
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
  android_clock_host_ = declare_parameter<std::string>("android_clock_host", "");
  const auto clock_interval_s = declare_parameter<double>("clock_ping_interval_s", 1.0);
  const auto clock_timeout_s = declare_parameter<double>("clock_response_timeout_s", 0.5);
  clock_fresh_after_s_ = declare_parameter<double>("clock_fresh_after_s", 3.0);
  const auto primary_port = declare_parameter<int>("primary_rtp_port", 5600);
  const auto payload_type = declare_parameter<int>("rtp_payload_type", 96);
  const auto latency_ms = declare_parameter<int>("rtp_latency_ms", 20);
  primary_topic_ = declare_parameter<std::string>("primary_topic", "/dji/primary/image_raw");
  context_topic_ = declare_parameter<std::string>("primary_context_topic", "/dji/primary/frame_context");
  navigation_topic_ = declare_parameter<std::string>("navigation_topic", "/dji/navigation/state");
  status_topic_ = declare_parameter<std::string>("status_topic", "/dji/edge/transport_status");
  diagnostics_topic_ = declare_parameter<std::string>("diagnostics_topic", "/dji/diagnostics");
  const auto diagnostics_period = declare_parameter<double>("diagnostics_period_s", 1.0);
  primary_stale_after_s_ = declare_parameter<double>("primary_stale_after_s", 1.0);
  const auto sender_correlation_s = declare_parameter<double>("sender_correlation_s", 2.0);
  const auto dashboard_port = declare_parameter<int>("dashboard_port", 8090);
  const auto dashboard_local_config = declare_parameter<std::string>(
    "dashboard_local_config", "/workspace/transport.local.yaml");
  const auto evidence_root = declare_parameter<std::string>("evidence_root", "/workspace/.runtime/evidence");
  health_evaluator_ = PrimaryHealthEvaluator(primary_stale_after_s_, sender_correlation_s);

  image_publisher_ = create_publisher<sensor_msgs::msg::Image>(primary_topic_, rclcpp::SensorDataQoS());
  context_publisher_ = create_publisher<dji_edge_transport::msg::FrameContext>(context_topic_, rclcpp::SensorDataQoS());
  navigation_publisher_ = create_publisher<dji_edge_transport::msg::NavigationState>(navigation_topic_, 10);
  status_publisher_ = create_publisher<msg::TransportStatus>(status_topic_, 10);
  diagnostics_publisher_ = create_publisher<diagnostic_msgs::msg::DiagnosticArray>(diagnostics_topic_, 10);

  video_ = std::make_unique<PrimaryVideoPipeline>(
    bind_host_, static_cast<std::uint16_t>(primary_port), payload_type, latency_ms, handoff_,
    [this]() { notify_frame_ready(); },
    [this](const std::string & error) { RCLCPP_ERROR(get_logger(), "Primary pipeline: %s", error.c_str()); });
  video_->start();
  delivery_thread_ = std::thread(&TransportNode::delivery_loop, this);

  telemetry_receiver_ = std::make_unique<UdpReceiver>(
    bind_host_, static_cast<std::uint16_t>(telemetry_port), max_json_bytes_,
    [this](const auto & bytes, auto receive_ns, const auto & peer) { on_telemetry(bytes, receive_ns, peer); });
  metadata_receiver_ = std::make_unique<UdpReceiver>(
    bind_host_, static_cast<std::uint16_t>(metadata_port), max_json_bytes_,
    [this](const auto & bytes, auto receive_ns, const auto & peer) { on_frame_metadata(bytes, receive_ns, peer); });
  clock_receiver_ = std::make_unique<UdpReceiver>(
    bind_host_, static_cast<std::uint16_t>(clock_port), max_json_bytes_,
    [this](const auto & bytes, auto receive_ns, const auto & peer) { on_clock(bytes, receive_ns, peer); });
  telemetry_receiver_->start();
  metadata_receiver_->start();
  clock_receiver_->start();
  clock_pinger_ = std::make_unique<ClockPinger>(android_clock_host_,
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
  evidence_writer_ = std::make_unique<SessionEvidenceWriter>(evidence_root);

  status_timer_ = create_wall_timer(
    std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::duration<double>(diagnostics_period)),
    [this]() { publish_status(); });
}

TransportNode::~TransportNode()
{
  {
    std::lock_guard<std::mutex> lock(delivery_mutex_);
    delivery_stopping_ = true;
  }
  delivery_cv_.notify_one();
  if (delivery_thread_.joinable()) delivery_thread_.join();
  if (telemetry_receiver_) telemetry_receiver_->stop();
  if (metadata_receiver_) metadata_receiver_->stop();
  if (clock_receiver_) clock_receiver_->stop();
  if (clock_pinger_) clock_pinger_->stop();
  if (dashboard_) dashboard_->stop();
  if (video_) video_->close();
}

void TransportNode::notify_frame_ready()
{
  {
    std::lock_guard<std::mutex> lock(delivery_mutex_);
    if (delivery_stopping_) return;
    delivery_pending_ = true;
  }
  delivery_cv_.notify_one();
}

void TransportNode::delivery_loop()
{
  std::unique_lock<std::mutex> lock(delivery_mutex_);
  while (true) {
    delivery_cv_.wait(lock, [this]() { return delivery_stopping_ || delivery_pending_; });
    if (delivery_stopping_) return;
    delivery_pending_ = false;
    lock.unlock();
    publish_frame();
    lock.lock();
  }
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

bool TransportNode::accept_session_locked(const std::string & session, const std::string & logical_stream,
  std::uint64_t sequence, const char * category)
{
  const auto decision = sessions_.observe(session, logical_stream, sequence);
  if (decision == SessionObservation::rejected_retired) {
    RCLCPP_DEBUG(get_logger(), "Rejected %s datagram from retired session", category);
    return false;
  }
  if (decision == SessionObservation::rejected_sequence) {
    RCLCPP_DEBUG(get_logger(), "Rejected %s datagram with non-monotonic sequence", category);
    return false;
  }
  if (decision == SessionObservation::transitioned) {
    source_time_state_.reset(session);
    latest_telemetry_.reset();
    latest_metadata_.reset();
    latest_health_.reset();
    last_metadata_receive_mono_ns_ = 0;
    metadata_count_ = 0;
    last_telemetry_receive_mono_ns_ = 0;
    telemetry_packet_count_ = 0;
    last_health_receive_mono_ns_ = 0;
    health_count_ = 0;
    latest_telemetry_peer_ = {};
    latest_metadata_peer_ = {};
    std::lock_guard<std::mutex> evidence_lock(evidence_state_mutex_);
    evidence_incident_active_ = false;
  }
  return true;
}

void TransportNode::on_telemetry(const std::vector<std::uint8_t> & bytes, std::uint64_t receive_ns,
  const UdpPeer & peer)
{
  std::string health_error;
  if (auto health = decode_health(bytes, max_json_bytes_, health_error)) {
    std::lock_guard<std::mutex> lock(state_mutex_);
    if (!accept_session_locked(health->session, "health:0", health->sequence, "health")) return;
    latest_health_ = std::move(*health);
    last_health_receive_mono_ns_ = receive_ns;
    ++health_count_;
    latest_telemetry_peer_ = peer;
    return;
  }
  std::string error;
  auto packet = decode_telemetry(bytes, max_json_bytes_, error);
  if (!packet) {
    log_protocol_error("telemetry", error);
    return;
  }
  std::optional<TelemetryPacket> navigation;
  {
    std::lock_guard<std::mutex> lock(state_mutex_);
    if (!accept_session_locked(packet->session, packet->type + ":0", packet->sequence, "telemetry")) return;
    latest_telemetry_ = *packet;
    source_time_state_.add(*packet);
    navigation = source_time_state_.latest();
    last_telemetry_receive_mono_ns_ = receive_ns;
    ++telemetry_packet_count_;
    latest_telemetry_peer_ = peer;
  }
  if (navigation) publish_navigation(*navigation);
}

void TransportNode::on_frame_metadata(const std::vector<std::uint8_t> & bytes, std::uint64_t receive_ns,
  const UdpPeer & peer)
{
  std::string error;
  auto packet = decode_frame_metadata(bytes, max_json_bytes_, error);
  if (!packet) {
    log_protocol_error("frame metadata", error);
    return;
  }
  std::lock_guard<std::mutex> lock(state_mutex_);
  if (!accept_session_locked(packet->session, "video_au:primary", packet->frame_seq, "metadata")) return;
  latest_metadata_ = std::move(*packet);
  last_metadata_receive_mono_ns_ = receive_ns;
  ++metadata_count_;
  latest_metadata_peer_ = peer;
}

void TransportNode::on_clock(const std::vector<std::uint8_t> & bytes, std::uint64_t,
  const UdpPeer &)
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
  auto image = std::make_unique<sensor_msgs::msg::Image>();
  image->header.stamp = ros_stamp();
  image->header.frame_id = "dji_primary_camera";
  image->width = frame->width;
  image->height = frame->height;
  image->encoding = "bgr8";
  image->is_bigendian = false;
  image->step = frame->width * 3;
  image->data = std::move(frame->bgr);
  const auto header = image->header;
  try {
    image_publisher_->publish(std::move(image));
  } catch (const rclcpp::exceptions::RCLError &) {
    ++publish_error_count_;
    return;
  }
  last_published_mono_ns_.store(now_ns());
  published_count_.fetch_add(1);

  const auto context = make_unavailable_primary_context(header, *frame);
  try {
    context_publisher_->publish(context);
  } catch (const rclcpp::exceptions::RCLError &) {
    ++publish_error_count_;
  }
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
  status.ingress_packet_count = snapshot.ingress_packet_count;
  status.ingress_byte_count = snapshot.ingress_byte_count;
  status.rtp_header_continuity_available = snapshot.rtp.header_continuity_available;
  status.rtp_sequence_gap_count = snapshot.rtp.sequence_gap_count;
  status.rtp_duplicate_count = snapshot.rtp.duplicate_count;
  status.rtp_reordered_count = snapshot.rtp.reordered_count;
  status.rtp_malformed_header_count = snapshot.rtp.malformed_header_count;
  status.jitterbuffer_drop_message_count = snapshot.jitterbuffer.message_count;
  status.jitterbuffer_too_late_count = snapshot.jitterbuffer.too_late_count;
  status.jitterbuffer_drop_on_latency_count = snapshot.jitterbuffer.drop_on_latency_count;
  status.jitterbuffer_malformed_message_count = snapshot.jitterbuffer.malformed_message_count;
  status.width = snapshot.width;
  status.height = snapshot.height;
  status.decoder_backend = snapshot.decoder_backend;
  status.decoder_reason = snapshot.decoder_reason;
  status.pipeline_error = snapshot.pipeline_error;
  const auto clock = clock_mapper_.estimate();
  const auto clock_last_success = clock_pinger_ ? clock_pinger_->last_success_mono_ns() : 0;
  const auto clock_current = clock.ready && clock_last_success > 0 &&
    age_seconds(current, clock_last_success) <= clock_fresh_after_s_;
  status.clock_ready = clock_current;
  status.clock_sample_count = clock.sample_count;
  status.clock_best_rtt_ns = clock.best_rtt_ns;
  status.clock_offset_android_minus_edge_ns = clock.offset_android_minus_edge_ns;
  if (clock_pinger_) {
    status.clock_attempt_count = clock_pinger_->attempts();
    status.clock_success_count = clock_pinger_->successes();
    status.clock_timeout_count = clock_pinger_->timeouts();
    status.clock_send_failure_count = clock_pinger_->send_failures();
    status.clock_receive_failure_count = clock_pinger_->receive_failures();
    status.clock_decode_failure_count = clock_pinger_->decode_failures();
    status.clock_unmatched_pong_count = clock_pinger_->unmatched_pongs();
    status.clock_last_rtt_ns = clock_pinger_->last_rtt_ns();
    status.clock_error = clock_pinger_->last_error();
  }
  if (last_rate_mono_ns_ != 0 && current > last_rate_mono_ns_) {
    const auto seconds = static_cast<double>(current - last_rate_mono_ns_) / 1e9;
    ingress_bitrate_bps_.store(static_cast<double>(
        counter_window_delta(snapshot.ingress_byte_count, last_rate_ingress_bytes_)) * 8.0 / seconds);
    decoded_fps_.store(static_cast<double>(
        counter_window_delta(snapshot.decoded_count, last_rate_decoded_count_)) / seconds);
    published_fps_.store(static_cast<double>(
        counter_window_delta(published_count_.load(), last_rate_published_count_)) / seconds);
    superseded_frame_rate_.store(static_cast<double>(
        counter_window_delta(status.dropped_old_frame_count, last_rate_dropped_count_)) / seconds);
    ingress_packets_window_.store(counter_window_delta(
        snapshot.ingress_packet_count, last_rate_ingress_packets_));
    ingress_bytes_window_.store(counter_window_delta(
        snapshot.ingress_byte_count, last_rate_ingress_bytes_));
    rtp_sequence_gaps_window_.store(counter_window_delta(
        snapshot.rtp.sequence_gap_count, last_rate_rtp_sequence_gaps_));
    rtp_duplicates_window_.store(counter_window_delta(
        snapshot.rtp.duplicate_count, last_rate_rtp_duplicates_));
    rtp_reordered_window_.store(counter_window_delta(
        snapshot.rtp.reordered_count, last_rate_rtp_reordered_));
    rtp_malformed_headers_window_.store(counter_window_delta(
        snapshot.rtp.malformed_header_count, last_rate_rtp_malformed_headers_));
    jitterbuffer_messages_window_.store(counter_window_delta(
        snapshot.jitterbuffer.message_count, last_rate_jitterbuffer_messages_));
    jitterbuffer_too_late_window_.store(counter_window_delta(
        snapshot.jitterbuffer.too_late_count, last_rate_jitterbuffer_too_late_));
    jitterbuffer_drop_on_latency_window_.store(counter_window_delta(
        snapshot.jitterbuffer.drop_on_latency_count, last_rate_jitterbuffer_drop_on_latency_));
    jitterbuffer_malformed_window_.store(counter_window_delta(
        snapshot.jitterbuffer.malformed_message_count, last_rate_jitterbuffer_malformed_));
  }
  status.ingress_bitrate_bps = ingress_bitrate_bps_.load();
  status.decoded_fps = decoded_fps_.load();
  status.published_fps = published_fps_.load();
  status.superseded_frame_rate = superseded_frame_rate_.load();
  status.ingress_packet_window = ingress_packets_window_.load();
  status.ingress_byte_window = ingress_bytes_window_.load();
  status.rtp_sequence_gap_window = rtp_sequence_gaps_window_.load();
  status.rtp_duplicate_window = rtp_duplicates_window_.load();
  status.rtp_reordered_window = rtp_reordered_window_.load();
  status.rtp_malformed_header_window = rtp_malformed_headers_window_.load();
  status.jitterbuffer_drop_message_window = jitterbuffer_messages_window_.load();
  status.jitterbuffer_too_late_window = jitterbuffer_too_late_window_.load();
  status.jitterbuffer_drop_on_latency_window = jitterbuffer_drop_on_latency_window_.load();
  status.jitterbuffer_malformed_message_window = jitterbuffer_malformed_window_.load();
  {
    std::lock_guard<std::mutex> lock(state_mutex_);
    status.session = sessions_.active_session();
    status.metadata_age_s = age_seconds(current, last_metadata_receive_mono_ns_);
    status.metadata_count = metadata_count_;
    status.sender_health_age_s = age_seconds(current, last_health_receive_mono_ns_);
    status.sender_health_count = health_count_;
    if (latest_health_) {
      status.android_primary_video_callbacks = latest_health_->primary_video_callbacks;
      status.android_primary_video_callback_drops = latest_health_->primary_video_callback_drops;
      status.android_primary_rtp_packets = latest_health_->primary_rtp_packets;
      status.android_primary_access_units = latest_health_->primary_access_units;
      status.android_telemetry_queue_drops = latest_health_->telemetry_queue_drops;
      status.android_telemetry_socket_errors = latest_health_->telemetry_socket_errors;
      status.android_video_socket_errors = latest_health_->video_socket_errors;
    }
  }
  last_rate_mono_ns_ = current;
  last_rate_ingress_packets_ = snapshot.ingress_packet_count;
  last_rate_ingress_bytes_ = snapshot.ingress_byte_count;
  last_rate_decoded_count_ = snapshot.decoded_count;
  last_rate_published_count_ = published_count_.load();
  last_rate_dropped_count_ = status.dropped_old_frame_count;
  last_rate_rtp_sequence_gaps_ = snapshot.rtp.sequence_gap_count;
  last_rate_rtp_duplicates_ = snapshot.rtp.duplicate_count;
  last_rate_rtp_reordered_ = snapshot.rtp.reordered_count;
  last_rate_rtp_malformed_headers_ = snapshot.rtp.malformed_header_count;
  last_rate_jitterbuffer_messages_ = snapshot.jitterbuffer.message_count;
  last_rate_jitterbuffer_too_late_ = snapshot.jitterbuffer.too_late_count;
  last_rate_jitterbuffer_drop_on_latency_ = snapshot.jitterbuffer.drop_on_latency_count;
  last_rate_jitterbuffer_malformed_ = snapshot.jitterbuffer.malformed_message_count;
  const auto assessment = health_evaluator_.evaluate({status.ingress_seen, status.ingress_age_s,
      status.decoded_age_s, status.published_age_s, status.sender_health_age_s,
      status.metadata_age_s, status.pipeline_error});
  switch (assessment.state) {
    case PrimaryHealthState::waiting_for_udp: status.state = msg::TransportStatus::WAITING_FOR_UDP; break;
    case PrimaryHealthState::waiting_for_keyframe: status.state = msg::TransportStatus::WAITING_FOR_KEYFRAME; break;
    case PrimaryHealthState::decoding: status.state = msg::TransportStatus::DECODING; break;
    case PrimaryHealthState::publishing: status.state = msg::TransportStatus::PUBLISHING; break;
    case PrimaryHealthState::pipeline_error: status.state = msg::TransportStatus::PIPELINE_ERROR; break;
    case PrimaryHealthState::delivery_stalled: status.state = msg::TransportStatus::DELIVERY_STALLED; break;
  }
  status.state_reason = assessment.reason;
  status_publisher_->publish(status);
  record_status_transition(status);

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

void TransportNode::record_status_transition(const msg::TransportStatus & status)
{
  if (!evidence_writer_ || status.session.empty()) return;
  std::lock_guard<std::mutex> lock(evidence_state_mutex_);
  const bool recovered = evidence_incident_active_ && status.state == msg::TransportStatus::PUBLISHING;
  const bool incident = !evidence_incident_active_ && status.state != msg::TransportStatus::PUBLISHING;
  if (!incident && !recovered) return;
  nlohmann::json event{{"v", 1}, {"type", recovered ? "primary_recovery" :
      (incident ? "primary_incident" : "primary_state")},
    {"session", status.session}, {"state", status.state}, {"reason", status.state_reason},
    {"ingress_age_s", status.ingress_age_s}, {"metadata_age_s", status.metadata_age_s},
    {"decoded_age_s", status.decoded_age_s}, {"published_age_s", status.published_age_s},
    {"sender_health_age_s", status.sender_health_age_s},
    {"rtp_header_continuity_available", status.rtp_header_continuity_available},
    {"ingress_packet_window", status.ingress_packet_window},
    {"ingress_byte_window", status.ingress_byte_window},
    {"rtp_sequence_gap_count", status.rtp_sequence_gap_count},
    {"rtp_sequence_gap_window", status.rtp_sequence_gap_window},
    {"rtp_duplicate_count", status.rtp_duplicate_count},
    {"rtp_duplicate_window", status.rtp_duplicate_window},
    {"rtp_reordered_count", status.rtp_reordered_count},
    {"rtp_reordered_window", status.rtp_reordered_window},
    {"rtp_malformed_header_count", status.rtp_malformed_header_count},
    {"rtp_malformed_header_window", status.rtp_malformed_header_window},
    {"jitterbuffer_drop_message_count", status.jitterbuffer_drop_message_count},
    {"jitterbuffer_drop_message_window", status.jitterbuffer_drop_message_window},
    {"jitterbuffer_too_late_count", status.jitterbuffer_too_late_count},
    {"jitterbuffer_too_late_window", status.jitterbuffer_too_late_window},
    {"jitterbuffer_drop_on_latency_count", status.jitterbuffer_drop_on_latency_count},
    {"jitterbuffer_drop_on_latency_window", status.jitterbuffer_drop_on_latency_window}};
  evidence_writer_->record(status.session, event.dump());
  evidence_incident_active_ = incident;
}

std::string TransportNode::dashboard_state() const
{
  const auto snapshot = video_ ? video_->snapshot() : VideoSnapshot{};
  const auto current = now_ns();
  const auto ingress_age_s = age_seconds(current, snapshot.last_rtp_mono_ns);
  const auto decoded_age_s = age_seconds(current, snapshot.last_decoded_mono_ns);
  const auto published_age_s = age_seconds(current, last_published_mono_ns_.load());
  const auto clock = clock_mapper_.estimate();
  const auto clock_last_success = clock_pinger_ ? clock_pinger_->last_success_mono_ns() : 0;
  const auto clock_current = clock.ready && clock_last_success > 0 &&
    age_seconds(current, clock_last_success) <= clock_fresh_after_s_;
  nlohmann::json connection;
  nlohmann::json sender;
  nlohmann::json primary;
  nlohmann::json telemetry;
  {
    std::lock_guard<std::mutex> lock(state_mutex_);
    connection = {{"session", sessions_.active_session()},
      {"telemetry_received", telemetry_receiver_ ? telemetry_receiver_->received() : 0},
      {"metadata_received", metadata_receiver_ ? metadata_receiver_->received() : 0},
      {"telemetry_peer_ipv4", latest_telemetry_peer_.ipv4},
      {"telemetry_peer_port", latest_telemetry_peer_.port},
      {"metadata_peer_ipv4", latest_metadata_peer_.ipv4},
      {"metadata_peer_port", latest_metadata_peer_.port}};
    sender = {{"health_age_s", age_seconds(current, last_health_receive_mono_ns_)},
      {"health_count", health_count_}};
    if (latest_health_) sender["counters"] = {{"primary_video_callbacks", latest_health_->primary_video_callbacks},
      {"primary_video_callback_drops", latest_health_->primary_video_callback_drops},
      {"primary_rtp_packets", latest_health_->primary_rtp_packets},
      {"primary_access_units", latest_health_->primary_access_units},
      {"telemetry_queue_drops", latest_health_->telemetry_queue_drops},
      {"telemetry_socket_errors", latest_health_->telemetry_socket_errors},
      {"video_socket_errors", latest_health_->video_socket_errors}};
    if (latest_health_) {
      const auto optional_value = [](const auto & value) -> nlohmann::json {
        return value ? nlohmann::json(*value) : nlohmann::json(nullptr);
      };
      sender["optional_counters"] = {{"primary_rtp_bytes", optional_value(latest_health_->primary_rtp_bytes)},
        {"telemetry_datagrams", optional_value(latest_health_->telemetry_datagrams)},
        {"telemetry_bytes", optional_value(latest_health_->telemetry_bytes)},
        {"metadata_datagrams", optional_value(latest_health_->metadata_datagrams)},
        {"metadata_bytes", optional_value(latest_health_->metadata_bytes)},
        {"clock_pongs_sent", optional_value(latest_health_->clock_pongs_sent)}};
    }
    primary = {{"metadata_age_s", age_seconds(current, last_metadata_receive_mono_ns_)},
      {"metadata_count", metadata_count_}};
    const auto navigation = source_time_state_.latest();
    telemetry = {{"telemetry_packets", telemetry_packet_count_},
      {"telemetry_age_s", age_seconds(current, last_telemetry_receive_mono_ns_)},
      {"telemetry_rejected", telemetry_receiver_ ? telemetry_receiver_->rejected() : 0},
      {"metadata_rejected", metadata_receiver_ ? metadata_receiver_->rejected() : 0},
      {"clock_received", clock_receiver_ ? clock_receiver_->received() : 0},
      {"clock_rejected", clock_receiver_ ? clock_receiver_->rejected() : 0},
      {"clock_age_s", age_seconds(current, clock_receiver_ ? clock_receiver_->last_receive_mono_ns() : 0)},
      {"navigation_available", navigation.has_value()}};
    if (navigation) telemetry.update({{"position_valid", navigation->position_valid},
      {"rtk_valid", navigation->rtk_valid}, {"latitude_deg", navigation->latitude_deg},
      {"longitude_deg", navigation->longitude_deg}, {"altitude_m", navigation->altitude_m},
      {"heading_deg", navigation->heading_deg}, {"gimbal_pitch_deg", navigation->gimbal_pitch_deg},
      {"gimbal_pitch_valid", navigation->gimbal_pitch_valid}, {"android_mono_ns", navigation->android_mono_ns}});
  }
  const auto assessment = health_evaluator_.evaluate({snapshot.last_rtp_mono_ns != 0, ingress_age_s,
      decoded_age_s, published_age_s, sender.value("health_age_s", -1.0),
      primary.value("metadata_age_s", -1.0), snapshot.pipeline_error});
  primary.update({{"state", primary_state_name(assessment.state)}, {"state_reason", assessment.reason},
    {"ingress_state", ingress_state(snapshot, ingress_age_s, primary_stale_after_s_)},
    {"decoder_state", decoder_state(snapshot, ingress_age_s, decoded_age_s, primary_stale_after_s_)},
    {"ros_delivery_state", ros_delivery_state(published_age_s, primary_stale_after_s_)},
    {"ingress_age_s", ingress_age_s}, {"decoded_age_s", decoded_age_s},
    {"published_age_s", published_age_s},
    {"decoded_count", snapshot.decoded_count}, {"published_count", published_count_.load()},
    {"dropped_old_frames", handoff_.dropped_old_frames()}, {"width", snapshot.width}, {"height", snapshot.height},
    {"ingress_packets", snapshot.ingress_packet_count}, {"ingress_bytes", snapshot.ingress_byte_count},
    {"ingress_bitrate_bps", ingress_bitrate_bps_.load()}, {"decoded_fps", decoded_fps_.load()},
    {"published_fps", published_fps_.load()}, {"superseded_frame_rate", superseded_frame_rate_.load()},
    {"rtp_header_continuity_available", snapshot.rtp.header_continuity_available},
    {"rtp_sequence_gaps", snapshot.rtp.sequence_gap_count}, {"rtp_duplicates", snapshot.rtp.duplicate_count},
    {"rtp_reordered", snapshot.rtp.reordered_count}, {"rtp_malformed_headers", snapshot.rtp.malformed_header_count},
    {"ingress_packets_window", ingress_packets_window_.load()},
    {"ingress_bytes_window", ingress_bytes_window_.load()},
    {"rtp_sequence_gaps_window", rtp_sequence_gaps_window_.load()},
    {"rtp_duplicates_window", rtp_duplicates_window_.load()},
    {"rtp_reordered_window", rtp_reordered_window_.load()},
    {"rtp_malformed_headers_window", rtp_malformed_headers_window_.load()},
    {"jitterbuffer_drop_messages", snapshot.jitterbuffer.message_count},
    {"jitterbuffer_too_late", snapshot.jitterbuffer.too_late_count},
    {"jitterbuffer_drop_on_latency", snapshot.jitterbuffer.drop_on_latency_count},
    {"jitterbuffer_malformed_messages", snapshot.jitterbuffer.malformed_message_count},
    {"jitterbuffer_drop_messages_window", jitterbuffer_messages_window_.load()},
    {"jitterbuffer_too_late_window", jitterbuffer_too_late_window_.load()},
    {"jitterbuffer_drop_on_latency_window", jitterbuffer_drop_on_latency_window_.load()},
    {"jitterbuffer_malformed_messages_window", jitterbuffer_malformed_window_.load()},
    {"decoder", snapshot.decoder_backend}, {"decoder_reason", snapshot.decoder_reason}, {"pipeline_error", snapshot.pipeline_error}});
  const auto state = !snapshot.pipeline_error.empty() ? "pipeline_error" :
    snapshot.last_rtp_mono_ns == 0 ? "waiting_for_udp" : snapshot.decoded_count == 0 ?
    "waiting_for_keyframe" : "publishing";
  nlohmann::json result{{"name", "DJI Transport Edge"}, {"connection", connection},
    {"android_sender", sender}, {"primary_edge", primary}, {"telemetry", telemetry},
    {"incident_clock", {{"state", state}, {"configured_host", android_clock_host_},
      {"evidence_dropped", evidence_writer_ ? evidence_writer_->dropped() : 0},
      {"evidence_write_errors", evidence_writer_ ? evidence_writer_->write_errors() : 0},
      {"ready", clock_current}, {"sample_count", clock.sample_count}, {"best_rtt_ns", clock.best_rtt_ns},
      {"offset_android_minus_edge_ns", clock.offset_android_minus_edge_ns},
      {"last_success_age_s", age_seconds(current, clock_last_success)},
      {"attempts", clock_pinger_ ? clock_pinger_->attempts() : 0},
      {"successes", clock_pinger_ ? clock_pinger_->successes() : 0},
      {"timeouts", clock_pinger_ ? clock_pinger_->timeouts() : 0},
      {"send_failures", clock_pinger_ ? clock_pinger_->send_failures() : 0},
      {"receive_failures", clock_pinger_ ? clock_pinger_->receive_failures() : 0},
      {"decode_failures", clock_pinger_ ? clock_pinger_->decode_failures() : 0},
      {"unmatched_pongs", clock_pinger_ ? clock_pinger_->unmatched_pongs() : 0},
      {"last_rtt_ns", clock_pinger_ ? clock_pinger_->last_rtt_ns() : 0},
      {"error", clock_pinger_ ? clock_pinger_->last_error() : ""}}}};
  return result.dump();
}

}  // namespace dji_edge_transport
