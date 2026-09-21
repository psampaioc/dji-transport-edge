#include "dji_edge_transport/protocol_decoder.hpp"
#include "dji_edge_transport/transport_node.hpp"

#include <gtest/gtest.h>

TEST(TransportContract, AcceptsOnlyPrimaryFrameMetadata)
{
  const std::string text = R"({"v":1,"type":"video_au","session":"s","feed":"fpv","frame_seq":4,"rtp_ssrc":8,"rtp_ts":9,"au_first_byte_rx_mono_ns":10})";
  std::string error;
  const std::vector<std::uint8_t> bytes(text.begin(), text.end());
  EXPECT_FALSE(dji_edge_transport::decode_frame_metadata(bytes, 1200, error).has_value());
  EXPECT_FALSE(error.empty());
}

TEST(TransportStatus, TreatsCounterResetAsAZeroWindow)
{
  EXPECT_EQ(dji_edge_transport::counter_window_delta(15, 10), 5u);
  EXPECT_EQ(dji_edge_transport::counter_window_delta(10, 10), 0u);
  EXPECT_EQ(dji_edge_transport::counter_window_delta(3, 10), 0u);
}
