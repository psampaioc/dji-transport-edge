#include "dji_edge_transport/protocol_decoder.hpp"

#include <gtest/gtest.h>

TEST(TransportContract, AcceptsOnlyPrimaryFrameMetadata)
{
  const std::string text = R"({"v":1,"type":"video_au","session":"s","feed":"fpv","frame_seq":4,"rtp_ssrc":8,"rtp_ts":9,"au_first_byte_rx_mono_ns":10})";
  std::string error;
  const std::vector<std::uint8_t> bytes(text.begin(), text.end());
  EXPECT_FALSE(dji_edge_transport::decode_frame_metadata(bytes, 1200, error).has_value());
  EXPECT_FALSE(error.empty());
}