#include "dji_edge_transport/frame_handoff.hpp"

#include <gtest/gtest.h>

TEST(FrameHandoff, KeepsOnlyNewestFrame)
{
  dji_edge_transport::FrameHandoff handoff;
  dji_edge_transport::DecodedFrame first;
  first.width = 1;
  first.bgr = {1};
  dji_edge_transport::DecodedFrame second;
  second.width = 2;
  second.bgr = {2};
  EXPECT_FALSE(handoff.replace(std::move(first)));
  EXPECT_TRUE(handoff.replace(std::move(second)));
  EXPECT_EQ(handoff.dropped_old_frames(), 1u);
  auto result = handoff.take();
  ASSERT_TRUE(result.has_value());
  EXPECT_EQ(result->width, 2u);
  EXPECT_FALSE(handoff.take().has_value());
}
