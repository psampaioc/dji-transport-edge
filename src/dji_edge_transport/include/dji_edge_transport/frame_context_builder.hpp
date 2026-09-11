#pragma once

#include "dji_edge_transport/frame_handoff.hpp"
#include "dji_edge_transport/msg/frame_context.hpp"

#include <std_msgs/msg/header.hpp>

namespace dji_edge_transport {

// A decoded image has no trustworthy RTP/AU identity until the decoder exposes
// one that is proven to round-trip from Android metadata.  This deliberately
// produces an unavailable context rather than guessing from the latest RTP.
dji_edge_transport::msg::FrameContext make_unavailable_primary_context(
  const std_msgs::msg::Header & header, const DecodedFrame & frame);

}  // namespace dji_edge_transport
