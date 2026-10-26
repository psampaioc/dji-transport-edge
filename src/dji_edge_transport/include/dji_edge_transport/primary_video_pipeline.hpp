#pragma once

#include "dji_edge_transport/frame_handoff.hpp"
#include "dji_edge_transport/rtp_ingress_tracker.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>

struct _GstElement;
struct _GstBus;
struct _GstStructure;
using GstElement = _GstElement;
using GstBus = _GstBus;
using GstStructure = _GstStructure;

namespace dji_edge_transport {

std::string format_gstreamer_error(
  const std::string & source, const std::string & message, const std::string & debug);

struct JitterbufferDropSnapshot {
  std::uint64_t message_count{0};
  std::uint64_t too_late_count{0};
  std::uint64_t drop_on_latency_count{0};
  std::uint64_t malformed_message_count{0};
};

// Parses scalar fields from GStreamer's documented jitterbuffer drop-msg only.
// It never retains media data or a GstMessage beyond the caller's scope.
bool observe_jitterbuffer_drop_message(
  const GstStructure * structure, JitterbufferDropSnapshot & snapshot);

struct VideoSnapshot {
  std::uint64_t last_rtp_mono_ns{0};
  std::uint64_t last_decoded_mono_ns{0};
  std::uint64_t ingress_packet_count{0};
  std::uint64_t ingress_byte_count{0};
  std::uint64_t decoded_count{0};
  RtpIngressSnapshot rtp;
  JitterbufferDropSnapshot jitterbuffer;
  std::uint32_t width{0};
  std::uint32_t height{0};
  std::string decoder_backend{"cpu"};
  std::string decoder_reason;
  std::string pipeline_error;
};

class PrimaryVideoPipeline {
public:
  PrimaryVideoPipeline(std::string bind_host, std::uint16_t port, int payload_type,
    int latency_ms, FrameHandoff & handoff, std::function<void()> frame_ready,
    std::function<void(const std::string &)> logger);
  ~PrimaryVideoPipeline();
  PrimaryVideoPipeline(const PrimaryVideoPipeline &) = delete;
  PrimaryVideoPipeline & operator=(const PrimaryVideoPipeline &) = delete;
  bool start();
  void poll_bus();
  void close();
  void accept_frame(DecodedFrame frame);
  void observe_rtp(const std::uint8_t * packet, std::size_t packet_bytes);
  VideoSnapshot snapshot() const;
  FrameHandoff & handoff() { return handoff_; }

private:
  friend class PrimaryVideoPipelineTestAccess;
  std::string description(const std::string & decoder) const;
  bool start_with_decoder(const std::string & decoder, const std::string & backend, const std::string & reason);
  void reset_pipeline();
  void set_error(const std::string & error);
  std::string bind_host_;
  std::uint16_t port_;
  int payload_type_;
  int latency_ms_;
  FrameHandoff & handoff_;
  std::function<void()> frame_ready_;
  std::function<void(const std::string &)> logger_;
  std::mutex pipeline_control_mutex_;
  mutable std::mutex mutex_;
  GstElement * pipeline_{nullptr};
  GstElement * sink_{nullptr};
  GstBus * bus_{nullptr};
  std::atomic<bool> closed_{false};
  std::uint64_t last_rtp_mono_ns_{0};
  std::uint64_t last_decoded_mono_ns_{0};
  std::uint64_t decoded_count_{0};
  std::uint64_t ingress_packet_count_{0};
  std::uint64_t ingress_byte_count_{0};
  RtpIngressTracker rtp_tracker_;
  JitterbufferDropSnapshot jitterbuffer_;
  std::uint32_t width_{0};
  std::uint32_t height_{0};
  std::string decoder_backend_{"cpu"};
  std::string decoder_reason_;
  std::string pipeline_error_;
  bool decoder_output_seen_{false};
  bool cpu_fallback_attempted_{false};
};

}  // namespace dji_edge_transport
