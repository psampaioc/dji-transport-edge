#pragma once

#include "dji_edge_transport/primary_video_pipeline.hpp"
#include "dji_edge_transport/primary_health_evaluator.hpp"
#include "dji_edge_transport/clock_mapper.hpp"
#include "dji_edge_transport/clock_pinger.hpp"
#include "dji_edge_transport/dashboard_server.hpp"
#include "dji_edge_transport/protocol_decoder.hpp"
#include "dji_edge_transport/session_tracker.hpp"
#include "dji_edge_transport/session_evidence_writer.hpp"
#include "dji_edge_transport/source_time_state.hpp"
#include "dji_edge_transport/udp_receiver.hpp"
#include "dji_edge_transport/msg/transport_status.hpp"
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <builtin_interfaces/msg/time.hpp>
#include <dji_edge_transport/msg/frame_context.hpp>
#include <dji_edge_transport/msg/navigation_state.hpp>
#include <dji_edge_transport/msg/telemetry_source_state.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>

#include <memory>
#include <condition_variable>
#include <mutex>
#include <optional>
#include <string>
#include <thread>

namespace dji_edge_transport {

constexpr std::uint64_t counter_window_delta(std::uint64_t current, std::uint64_t previous)
{
  return current >= previous ? current - previous : 0;
}

class TransportNode : public rclcpp::Node {
public:
  TransportNode();
  ~TransportNode() override;

private:
  builtin_interfaces::msg::Time ros_stamp();
  void on_telemetry(const std::vector<std::uint8_t> & bytes, std::uint64_t receive_ns,
    const UdpPeer & peer);
  void on_frame_metadata(const std::vector<std::uint8_t> & bytes, std::uint64_t receive_ns,
    const UdpPeer & peer);
  void on_clock(const std::vector<std::uint8_t> & bytes, std::uint64_t receive_ns,
    const UdpPeer & peer);
  void publish_frame();
  void notify_frame_ready();
  void delivery_loop();
  void publish_status();
  void publish_navigation(const TelemetryPacket & packet);
  void publish_telemetry_source(const TelemetryPacket & packet, std::uint64_t receive_ns);
  void log_protocol_error(const std::string & category, const std::string & error);
  bool accept_session_locked(const std::string & session, const std::string & logical_stream,
    std::uint64_t sequence, const char * category);
  std::string dashboard_state() const;
  void record_status_transition(const msg::TransportStatus & status);

  std::string bind_host_;
  std::string android_clock_host_;
  double clock_fresh_after_s_{3.0};
  double primary_stale_after_s_{1.0};
  std::size_t max_json_bytes_;
  std::string primary_topic_;
  std::string context_topic_;
  std::string navigation_topic_;
  std::string flight_topic_;
  std::string rtk_topic_;
  std::string gimbal_topic_;
  std::string status_topic_;
  std::string diagnostics_topic_;
  std::unique_ptr<UdpReceiver> telemetry_receiver_;
  std::unique_ptr<UdpReceiver> metadata_receiver_;
  std::unique_ptr<UdpReceiver> clock_receiver_;
  std::unique_ptr<ClockPinger> clock_pinger_;
  std::unique_ptr<DashboardServer> dashboard_;
  std::unique_ptr<SessionEvidenceWriter> evidence_writer_;
  FrameHandoff handoff_;
  std::unique_ptr<PrimaryVideoPipeline> video_;
  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr image_publisher_;
  rclcpp::Publisher<dji_edge_transport::msg::FrameContext>::SharedPtr context_publisher_;
  rclcpp::Publisher<dji_edge_transport::msg::NavigationState>::SharedPtr navigation_publisher_;
  rclcpp::Publisher<dji_edge_transport::msg::TelemetrySourceState>::SharedPtr flight_publisher_;
  rclcpp::Publisher<dji_edge_transport::msg::TelemetrySourceState>::SharedPtr rtk_publisher_;
  rclcpp::Publisher<dji_edge_transport::msg::TelemetrySourceState>::SharedPtr gimbal_publisher_;
  rclcpp::Publisher<msg::TransportStatus>::SharedPtr status_publisher_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr diagnostics_publisher_;
  rclcpp::TimerBase::SharedPtr status_timer_;
  std::mutex delivery_mutex_;
  std::condition_variable delivery_cv_;
  bool delivery_pending_{false};
  bool delivery_stopping_{false};
  std::thread delivery_thread_;
  mutable std::mutex state_mutex_;
  std::mutex evidence_state_mutex_;
  std::optional<TelemetryPacket> latest_telemetry_;
  struct TelemetryObservation {
    TelemetryPacket packet;
    std::uint64_t edge_receive_mono_ns{0};
  };
  std::optional<TelemetryObservation> latest_flight_;
  std::optional<TelemetryObservation> latest_rtk_;
  std::optional<TelemetryObservation> latest_gimbal_;
  std::optional<FrameMetadata> latest_metadata_;
  std::optional<HealthPacket> latest_health_;
  SessionTracker sessions_;
  SourceTimeState source_time_state_;
  std::uint64_t last_metadata_receive_mono_ns_{0};
  std::uint64_t metadata_count_{0};
  std::uint64_t last_telemetry_receive_mono_ns_{0};
  std::uint64_t telemetry_packet_count_{0};
  std::uint64_t last_health_receive_mono_ns_{0};
  std::uint64_t health_count_{0};
  UdpPeer latest_telemetry_peer_;
  UdpPeer latest_metadata_peer_;
  ClockMapper clock_mapper_;
  std::atomic<std::uint64_t> last_published_mono_ns_{0};
  std::atomic<std::uint64_t> published_count_{0};
  std::uint64_t publish_error_count_{0};
  std::uint64_t last_rate_mono_ns_{0};
  std::uint64_t last_rate_ingress_packets_{0};
  std::uint64_t last_rate_ingress_bytes_{0};
  std::uint64_t last_rate_decoded_count_{0};
  std::uint64_t last_rate_published_count_{0};
  std::uint64_t last_rate_dropped_count_{0};
  std::uint64_t last_rate_rtp_sequence_gaps_{0};
  std::uint64_t last_rate_rtp_duplicates_{0};
  std::uint64_t last_rate_rtp_reordered_{0};
  std::uint64_t last_rate_rtp_malformed_headers_{0};
  std::uint64_t last_rate_jitterbuffer_messages_{0};
  std::uint64_t last_rate_jitterbuffer_too_late_{0};
  std::uint64_t last_rate_jitterbuffer_drop_on_latency_{0};
  std::uint64_t last_rate_jitterbuffer_malformed_{0};
  std::atomic<double> ingress_bitrate_bps_{0.0};
  std::atomic<double> decoded_fps_{0.0};
  std::atomic<double> published_fps_{0.0};
  std::atomic<double> superseded_frame_rate_{0.0};
  std::atomic<std::uint64_t> ingress_packets_window_{0};
  std::atomic<std::uint64_t> ingress_bytes_window_{0};
  std::atomic<std::uint64_t> rtp_sequence_gaps_window_{0};
  std::atomic<std::uint64_t> rtp_duplicates_window_{0};
  std::atomic<std::uint64_t> rtp_reordered_window_{0};
  std::atomic<std::uint64_t> rtp_malformed_headers_window_{0};
  std::atomic<std::uint64_t> jitterbuffer_messages_window_{0};
  std::atomic<std::uint64_t> jitterbuffer_too_late_window_{0};
  std::atomic<std::uint64_t> jitterbuffer_drop_on_latency_window_{0};
  std::atomic<std::uint64_t> jitterbuffer_malformed_window_{0};
  PrimaryHealthEvaluator health_evaluator_{1.0, 2.0};
  bool evidence_incident_active_{false};
};

}  // namespace dji_edge_transport
