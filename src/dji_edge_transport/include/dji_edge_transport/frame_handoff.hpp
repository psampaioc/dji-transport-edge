#pragma once

#include <cstdint>
#include <mutex>
#include <optional>
#include <utility>
#include <vector>

namespace dji_edge_transport {

struct DecodedFrame {
  std::uint32_t width{0};
  std::uint32_t height{0};
  std::uint64_t decoded_mono_ns{0};
  std::vector<std::uint8_t> bgr;
};

class FrameHandoff {
public:
  bool replace(DecodedFrame frame);
  std::optional<DecodedFrame> take();
  void clear();
  std::uint64_t dropped_old_frames() const;

private:
  mutable std::mutex mutex_;
  std::optional<DecodedFrame> frame_;
  std::uint64_t dropped_old_frames_{0};
};

}  // namespace dji_edge_transport
