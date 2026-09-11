#pragma once

#include "dji_edge_transport/frame_handoff.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>

struct _GstElement;
struct _GstBus;
using GstElement = _GstElement;
using GstBus = _GstBus;

namespace dji_edge_transport {

struct VideoSnapshot {
  std::uint64_t last_rtp_mono_ns{0};
  std::uint64_t last_decoded_mono_ns{0};
  std::uint64_t decoded_count{0};
  std::uint32_t width{0};
  std::uint32_t height{0};
  std::string decoder_backend{"cpu"};
  std::string decoder_reason;
  std::string pipeline_error;
};

class PrimaryVideoPipeline {
public:
  PrimaryVideoPipeline(std::string bind_host, std::uint16_t port, int payload_type,
    int latency_ms, FrameHandoff & handoff, std::function<void(const std::string &)> logger);
  ~PrimaryVideoPipeline();
  PrimaryVideoPipeline(const PrimaryVideoPipeline &) = delete;
  PrimaryVideoPipeline & operator=(const PrimaryVideoPipeline &) = delete;
  bool start();
  void poll_bus();
  void close();
  void accept_frame(DecodedFrame frame);
  void observe_rtp();
  VideoSnapshot snapshot() const;
  FrameHandoff & handoff() { return handoff_; }

private:
  std::string description(const std::string & decoder) const;
  bool start_with_decoder(const std::string & decoder, const std::string & backend, const std::string & reason);
  void set_error(const std::string & error);
  std::string bind_host_;
  std::uint16_t port_;
  int payload_type_;
  int latency_ms_;
  FrameHandoff & handoff_;
  std::function<void(const std::string &)> logger_;
  mutable std::mutex mutex_;
  GstElement * pipeline_{nullptr};
  GstElement * sink_{nullptr};
  GstBus * bus_{nullptr};
  std::atomic<bool> closed_{false};
  std::uint64_t last_rtp_mono_ns_{0};
  std::uint64_t last_decoded_mono_ns_{0};
  std::uint64_t decoded_count_{0};
  std::uint32_t width_{0};
  std::uint32_t height_{0};
  std::string decoder_backend_{"cpu"};
  std::string decoder_reason_;
  std::string pipeline_error_;
};

}  // namespace dji_edge_transport
