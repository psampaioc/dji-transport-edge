#include "dji_edge_transport/frame_context_builder.hpp"

#include <gtest/gtest.h>

TEST(FrameContextBuilder, WithholdsIdentityUntilAssociationIsProven)
{
  dji_edge_transport::DecodedFrame frame;
  frame.decoded_mono_ns = 42;
  std_msgs::msg::Header header;
  header.frame_id = "dji_primary_camera";

  const auto context = dji_edge_transport::make_unavailable_primary_context(header, frame);

  EXPECT_EQ(context.feed, "primary");
  EXPECT_EQ(context.association_quality,
    dji_edge_transport::msg::FrameContext::ASSOCIATION_UNAVAILABLE);
  EXPECT_EQ(context.frame_seq, 0u);
  EXPECT_EQ(context.rtp_ssrc, 0u);
  EXPECT_EQ(context.rtp_ts, 0u);
  EXPECT_EQ(context.edge_decoded_mono_ns, 42u);
  EXPECT_NE(context.association_reason.find("no proven"), std::string::npos);
}
