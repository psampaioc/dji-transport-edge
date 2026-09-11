#include "dji_edge_transport/frame_handoff.hpp"

namespace dji_edge_transport {

bool FrameHandoff::replace(DecodedFrame frame)
{
  std::lock_guard<std::mutex> lock(mutex_);
  const bool dropped = frame_.has_value();
  if (dropped) {
    ++dropped_old_frames_;
  }
  frame_ = std::move(frame);
  return dropped;
}

std::optional<DecodedFrame> FrameHandoff::take()
{
  std::lock_guard<std::mutex> lock(mutex_);
  if (!frame_) {
    return std::nullopt;
  }
  auto result = std::move(frame_);
  frame_.reset();
  return result;
}

void FrameHandoff::clear()
{
  std::lock_guard<std::mutex> lock(mutex_);
  frame_.reset();
}

std::uint64_t FrameHandoff::dropped_old_frames() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  return dropped_old_frames_;
}

}  // namespace dji_edge_transport
