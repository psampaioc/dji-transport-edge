#include "dji_edge_transport/frame_context_builder.hpp"

namespace dji_edge_transport {

dji_edge_transport::msg::FrameContext make_unavailable_primary_context(
  const std_msgs::msg::Header & header, const DecodedFrame & frame)
{
  dji_edge_transport::msg::FrameContext context;
  context.header = header;
  context.feed = "primary";
  context.association_quality = dji_edge_transport::msg::FrameContext::ASSOCIATION_UNAVAILABLE;
  context.association_reason =
    "Decoded Primary image has no proven Access Unit identity; context withheld";
  context.edge_decoded_mono_ns = frame.decoded_mono_ns;
  return context;
}

}  // namespace dji_edge_transport
