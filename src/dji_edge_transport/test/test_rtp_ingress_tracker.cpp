#include "dji_edge_transport/rtp_ingress_tracker.hpp"

#include <gtest/gtest.h>

#include <array>

namespace {
std::array<std::uint8_t, 12> packet(std::uint16_t sequence, std::uint8_t payload_type = 96)
{
  return {0x80, payload_type, static_cast<std::uint8_t>(sequence >> 8), static_cast<std::uint8_t>(sequence),
    0, 0, 0, 1, 0, 0, 0, 2};
}
}  // namespace

TEST(RtpIngressTracker, TracksSequenceWithoutRetainingPayload)
{
  dji_edge_transport::RtpIngressTracker tracker(96);
  auto first = packet(65535);
  auto second = packet(1);
  tracker.observe(first.data(), first.size());
  tracker.observe(second.data(), second.size());
  const auto snapshot = tracker.snapshot();
  EXPECT_TRUE(snapshot.header_continuity_available);
  EXPECT_EQ(snapshot.sequence_gap_count, 1u);
  EXPECT_EQ(snapshot.ssrc, 2u);
}

TEST(RtpIngressTracker, RejectsMalformedOrWrongPayloadHeaders)
{
  dji_edge_transport::RtpIngressTracker tracker(96);
  auto wrong_payload = packet(1, 97);
  tracker.observe(wrong_payload.data(), wrong_payload.size());
  tracker.observe(nullptr, 0);
  const auto snapshot = tracker.snapshot();
  EXPECT_FALSE(snapshot.header_continuity_available);
  EXPECT_EQ(snapshot.malformed_header_count, 2u);
}

TEST(RtpIngressTracker, ReorderedPacketsDoNotMoveSequenceHighWatermarkBackwards)
{
  dji_edge_transport::RtpIngressTracker tracker(96);
  for (const auto sequence : std::array<std::uint16_t, 4>{10, 12, 11, 13}) {
    auto datagram = packet(sequence);
    tracker.observe(datagram.data(), datagram.size());
  }
  const auto snapshot = tracker.snapshot();
  EXPECT_EQ(snapshot.sequence_gap_count, 1u);
  EXPECT_EQ(snapshot.reordered_count, 1u);
}
