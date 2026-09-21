#include "dji_edge_transport/primary_video_pipeline.hpp"

#include <gtest/gtest.h>

#include <gst/gst.h>

#include <atomic>
#include <array>

namespace {
std::array<std::uint8_t, 12> rtp_packet(std::uint16_t sequence)
{
  return {0x80, 96, static_cast<std::uint8_t>(sequence >> 8), static_cast<std::uint8_t>(sequence),
    0, 0, 0, 1, 0, 0, 0, 2};
}
}  // namespace

TEST(PrimaryVideoPipeline, TracksIngressWithoutRetainingPayload)
{
  dji_edge_transport::FrameHandoff handoff;
  std::atomic<std::uint64_t> notifications{0};
  dji_edge_transport::PrimaryVideoPipeline pipeline(
    "0.0.0.0", 5600, 96, 20, handoff, [&notifications]() { ++notifications; },
    [](const std::string &) {});

  auto first = rtp_packet(1);
  auto second = rtp_packet(2);
  pipeline.observe_rtp(first.data(), 1200);
  pipeline.observe_rtp(second.data(), 42);

  const auto snapshot = pipeline.snapshot();
  EXPECT_NE(snapshot.last_rtp_mono_ns, 0u);
  EXPECT_EQ(snapshot.ingress_packet_count, 2u);
  EXPECT_EQ(snapshot.ingress_byte_count, 1242u);
  EXPECT_EQ(snapshot.decoded_count, 0u);
}

TEST(PrimaryVideoPipeline, SignalsOnlyAfterDecodedFrameReplacesHandoff)
{
  dji_edge_transport::FrameHandoff handoff;
  std::atomic<std::uint64_t> notifications{0};
  dji_edge_transport::PrimaryVideoPipeline pipeline(
    "0.0.0.0", 5600, 96, 20, handoff, [&notifications]() { ++notifications; },
    [](const std::string &) {});
  dji_edge_transport::DecodedFrame frame;
  frame.width = 1;
  frame.height = 1;
  frame.decoded_mono_ns = 42;
  frame.bgr = {0, 0, 0};

  pipeline.accept_frame(std::move(frame));

  EXPECT_EQ(notifications.load(), 1u);
  ASSERT_TRUE(handoff.take().has_value());
}

TEST(PrimaryVideoPipeline, FormatsPipelineErrorWithSourceAndBoundedDebug)
{
  const std::string debug(1100, 'x');
  const auto error = dji_edge_transport::format_gstreamer_error(
    "udpsrc0", "Internal data stream error.", debug + "\nignored");

  EXPECT_NE(error.find("GStreamer [udpsrc0]: Internal data stream error."), std::string::npos);
  EXPECT_NE(error.find("[truncated]"), std::string::npos);
  EXPECT_EQ(error.find('\n'), std::string::npos);
}

TEST(PrimaryVideoPipeline, AccumulatesOnlyDocumentedJitterbufferDropMessages)
{
  auto * valid = gst_structure_new("drop-msg",
      "num-too-late", G_TYPE_UINT64, static_cast<guint64>(3),
      "num-drop-on-latency", G_TYPE_UINT64, static_cast<guint64>(5),
      nullptr);
  dji_edge_transport::JitterbufferDropSnapshot snapshot;

  EXPECT_TRUE(dji_edge_transport::observe_jitterbuffer_drop_message(valid, snapshot));
  EXPECT_EQ(snapshot.message_count, 1u);
  EXPECT_EQ(snapshot.too_late_count, 3u);
  EXPECT_EQ(snapshot.drop_on_latency_count, 5u);

  auto * incomplete = gst_structure_new("drop-msg",
      "num-too-late", G_TYPE_UINT64, static_cast<guint64>(1),
      nullptr);
  EXPECT_FALSE(dji_edge_transport::observe_jitterbuffer_drop_message(incomplete, snapshot));
  EXPECT_EQ(snapshot.message_count, 1u);

  auto * unrelated = gst_structure_new_empty("unrelated");
  EXPECT_FALSE(dji_edge_transport::observe_jitterbuffer_drop_message(unrelated, snapshot));
  EXPECT_EQ(snapshot.message_count, 1u);
  EXPECT_EQ(snapshot.too_late_count, 3u);
  EXPECT_EQ(snapshot.drop_on_latency_count, 5u);

  gst_structure_free(valid);
  gst_structure_free(incomplete);
  gst_structure_free(unrelated);
}
