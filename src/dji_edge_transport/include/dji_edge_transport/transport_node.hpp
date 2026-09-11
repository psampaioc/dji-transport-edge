#pragma once

#include "dji_edge_transport/primary_video_pipeline.hpp"
#include "dji_edge_transport/clock_mapper.hpp"
#include "dji_edge_transport/clock_pinger.hpp"
#include "dji_edge_transport/dashboard_server.hpp"
#include "dji_edge_transport/protocol_decoder.hpp"
#include "dji_edge_transport/source_time_state.hpp"
#include "dji_edge_transport/udp_receiver.hpp"
#include "dji_edge_transport/msg/transport_status.hpp"
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <builtin_interfaces/msg/time.hpp>
#include <dji_edge_transport/msg/frame_context.hpp>
#include <dji_edge_transport/msg/navigation_state.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>

#include <memory>
#include <mutex>
#include <optional>
#include <string>

namespace dji_edge_transport {

class TransportNode : public rclcpp::Node {
public:
  TransportNode();
  ~TransportNode() override;

private:
  builtin_interfaces::msg::Time ros_stamp();
  void on_telemetry(const std::vector<std::uint8_t> & bytes, std::uint64_t receive_ns);
  void on_frame_metadata(const std::vector<std::uint8_t> & bytes, std::uint64_t receive_ns);
  void on_clock(const std::vector<std::uint8_t> & bytes, std::uint64_t receive_ns);
  void publish_frame();
  void publish_status();
  void publish_navigation(const TelemetryPacket & packet);
  void log_protocol_error(const std::string & category, const std::string & error);
  std::string dashboard_state() const;

  std::string bind_host_;
  std::size_t max_json_bytes_;
  std::string primary_topic_;
  std::string context_topic_;
  std::string navigation_topic_;
  std::string status_topic_;
  std::string diagnostics_topic_;
  std::unique_ptr<UdpReceiver> telemetry_receiver_;
  std::unique_ptr<UdpReceiver> metadata_receiver_;
  std::unique_ptr<UdpReceiver> clock_receiver_;
  std::unique_ptr<ClockPinger> clock_pinger_;
  std::unique_ptr<DashboardServer> dashboard_;
  FrameHandoff handoff_;
  std::unique_ptr<PrimaryVideoPipeline> video_;
  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr image_publisher_;
  rclcpp::Publisher<dji_edge_transport::msg::FrameContext>::SharedPtr context_publisher_;
  rclcpp::Publisher<dji_edge_transport::msg::NavigationState>::SharedPtr navigation_publisher_;
  rclcpp::Publisher<msg::TransportStatus>::SharedPtr status_publisher_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diagnostics_publisher_;
  rclcpp::TimerBase::SharedPtr frame_timer_;
  rclcpp::TimerBase::SharedPtr status_timer_;
  std::mutex state_mutex_;
  std::optional<TelemetryPacket> latest_telemetry_;
  std::optional<FrameMetadata> latest_metadata_;
  SourceTimeState source_time_state_;
  ClockMapper clock_mapper_;
  std::atomic<std::uint64_t> last_published_mono_ns_{0};
  std::atomic<std::uint64_t> published_count_{0};
  std::uint64_t publish_error_count_{0};
  std::uint64_t last_rate_mono_ns_{0};
  std::uint64_t last_rate_decoded_count_{0};
  std::uint64_t last_rate_published_count_{0};
};

}  // namespace dji_edge_transport
