#include "dji_edge_transport/rtp_ingress_tracker.hpp"

#include <gtest/gtest.h>

#include <array>
#include <utility>

namespace {
std::array<std::uint8_t, 12> packet(
  std::uint16_t sequence, std::uint8_t payload_type = 96, std::uint32_t ssrc = 2)
{
  return {0x80, payload_type, static_cast<std::uint8_t>(sequence >> 8), static_cast<std::uint8_t>(sequence),
    0, 0, 0, 1, static_cast<std::uint8_t>(ssrc >> 24), static_cast<std::uint8_t>(ssrc >> 16),
    static_cast<std::uint8_t>(ssrc >> 8), static_cast<std::uint8_t>(ssrc)};
}
}  // namespace

TEST(RtpIngressTracker, TracksSequenceWithoutRetainingPayload)
{
  dji_edge_transport::RtpIngressTracker tracker(96);
  for (const auto sequence : std::array<std::uint16_t, 3>{65535, 0, 1}) {
    auto datagram = packet(sequence);
    tracker.observe(datagram.data(), datagram.size());
  }
  const auto snapshot = tracker.snapshot();
  EXPECT_TRUE(snapshot.header_continuity_available);
  EXPECT_EQ(snapshot.sequence_gap_count, 0u);
  EXPECT_EQ(snapshot.sequence_wrap_count, 1u);
  EXPECT_EQ(snapshot.ssrc_epoch_count, 1u);
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
  EXPECT_EQ(snapshot.sequence_gap_count, 0u);
  EXPECT_EQ(snapshot.reordered_count, 1u);
  EXPECT_EQ(snapshot.late_fill_count, 1u);
}

TEST(RtpIngressTracker, LateFillDoesNotBecomeAFinalizedGap)
{
  dji_edge_transport::RtpIngressTracker tracker(96);
  for (const auto sequence : std::array<std::uint16_t, 4>{100, 102, 101, 103}) {
    auto datagram = packet(sequence);
    tracker.observe(datagram.data(), datagram.size());
  }
  EXPECT_EQ(tracker.snapshot().sequence_gap_count, 0u);
  EXPECT_EQ(tracker.snapshot().late_fill_count, 1u);
}

TEST(RtpIngressTracker, FinalizesGapOnlyAfterReorderHorizon)
{
  dji_edge_transport::RtpIngressTracker tracker(96);
  for (const auto sequence : std::array<std::uint16_t, 2>{100, 102}) {
    auto datagram = packet(sequence);
    tracker.observe(datagram.data(), datagram.size());
  }
  EXPECT_EQ(tracker.snapshot().sequence_gap_count, 0u);

  for (std::uint16_t sequence = 103; sequence <= 165; ++sequence) {
    auto datagram = packet(sequence);
    tracker.observe(datagram.data(), datagram.size());
  }
  EXPECT_EQ(tracker.snapshot().sequence_gap_count, 1u);
}

TEST(RtpIngressTracker, FinalizesPendingGapsOnSsrcChangeAndAcceptsZeroSsrc)
{
  dji_edge_transport::RtpIngressTracker tracker(96);
  for (const auto & [sequence, ssrc] : std::array<std::pair<std::uint16_t, std::uint32_t>, 3>{
      std::pair<std::uint16_t, std::uint32_t>{10, 0}, {12, 0}, {3, 9}})
  {
    auto datagram = packet(sequence, 96, ssrc);
    tracker.observe(datagram.data(), datagram.size());
  }
  const auto snapshot = tracker.snapshot();
  EXPECT_EQ(snapshot.sequence_gap_count, 1u);
  EXPECT_EQ(snapshot.ssrc_epoch_count, 2u);
  EXPECT_EQ(snapshot.ssrc, 9u);
}

TEST(RtpIngressTracker, PacketOlderThanHorizonDoesNotMoveHighWatermark)
{
  dji_edge_transport::RtpIngressTracker tracker(96);
  for (const auto sequence : std::array<std::uint16_t, 3>{100, 200, 101}) {
    auto datagram = packet(sequence);
    tracker.observe(datagram.data(), datagram.size());
  }
  auto next = packet(201);
  tracker.observe(next.data(), next.size());
  const auto snapshot = tracker.snapshot();
  EXPECT_EQ(snapshot.sequence_gap_count, 37u);
  EXPECT_EQ(snapshot.pending_gap_count, 62u);
  EXPECT_EQ(snapshot.reordered_count, 1u);
  EXPECT_EQ(snapshot.late_fill_count, 0u);
}

TEST(RtpIngressTracker, DistinguishesDuplicateFromReorderedPacket)
{
  dji_edge_transport::RtpIngressTracker tracker(96);
  for (const auto sequence : std::array<std::uint16_t, 4>{10, 11, 11, 10}) {
    auto datagram = packet(sequence);
    tracker.observe(datagram.data(), datagram.size());
  }
  const auto snapshot = tracker.snapshot();
  EXPECT_EQ(snapshot.sequence_gap_count, 0u);
  EXPECT_EQ(snapshot.duplicate_count, 2u);
  EXPECT_EQ(snapshot.reordered_count, 0u);
}
