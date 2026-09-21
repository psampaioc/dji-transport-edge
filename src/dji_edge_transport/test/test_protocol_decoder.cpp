#include "dji_edge_transport/protocol_decoder.hpp"

#include <gtest/gtest.h>
#include <fstream>
#include <string>
#include <vector>

namespace {
std::vector<std::uint8_t> fixture(const std::string & name)
{
  const std::string path = std::string(DJI_EDGE_TRANSPORT_SOURCE_DIR) +
    "/../../docs/transport-contract-fixtures/primary-only/" + name;
  std::ifstream input(path);
  EXPECT_TRUE(input.good()) << path;
  const std::string text((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
  return {text.begin(), text.end()};
}
}  // namespace

TEST(ProtocolDecoder, DecodesCommittedAndroidContractFixtures)
{
  std::string error;
  const auto flight = dji_edge_transport::decode_telemetry(fixture("flight.json"), 1200, error);
  ASSERT_TRUE(flight.has_value()) << error;
  EXPECT_EQ(flight->session, "fixture-session");
  EXPECT_TRUE(flight->position_valid);

  const auto health = dji_edge_transport::decode_health(fixture("health.json"), 1200, error);
  ASSERT_TRUE(health.has_value()) << error;
  EXPECT_EQ(health->session, "fixture-session");
  EXPECT_EQ(health->primary_rtp_packets, 20u);

  const auto rtk = dji_edge_transport::decode_telemetry(fixture("rtk.json"), 1200, error);
  ASSERT_TRUE(rtk.has_value()) << error;
  EXPECT_TRUE(rtk->rtk_valid);

  const auto gimbal = dji_edge_transport::decode_telemetry(fixture("gimbal.json"), 1200, error);
  ASSERT_TRUE(gimbal.has_value()) << error;
  EXPECT_TRUE(gimbal->gimbal_pitch_valid);

  const auto metadata = dji_edge_transport::decode_frame_metadata(fixture("video_au.json"), 1200, error);
  ASSERT_TRUE(metadata.has_value()) << error;
  EXPECT_EQ(metadata->feed, "primary");

  const auto clock = dji_edge_transport::decode_clock(fixture("clock_pong.json"), 1200, error);
  ASSERT_TRUE(clock.has_value()) << error;
  EXPECT_TRUE(clock->is_pong);
}

TEST(ProtocolDecoder, AcceptsPrimaryFrameMetadata)
{
  const std::string text = R"({"v":1,"type":"video_au","session":"s","feed":"primary","frame_seq":4,"rtp_ssrc":8,"rtp_ts":9,"au_first_byte_rx_mono_ns":10,"au_complete_rx_mono_ns":11})";
  std::string error;
  const std::vector<std::uint8_t> bytes(text.begin(), text.end());
  auto result = dji_edge_transport::decode_frame_metadata(bytes, 1200, error);
  ASSERT_TRUE(result.has_value()) << error;
  EXPECT_EQ(result->frame_seq, 4u);
  EXPECT_EQ(result->feed, "primary");
}

TEST(ProtocolDecoder, RejectsOversizedDatagram)
{
  std::string error;
  const std::vector<std::uint8_t> bytes(5, 'x');
  EXPECT_FALSE(dji_edge_transport::decode_telemetry(bytes, 4, error).has_value());
  EXPECT_FALSE(error.empty());
}

TEST(ProtocolDecoder, DecodesCanonicalCompactFlightFields)
{
  const std::string text = R"({"v":1,"type":"flight","session":"s","stream":"flight:0","seq":4,"rx_mono_ns":100,"data":{"fields":{"aircraft.latitude_deg":{"value":38.7,"valid":true},"aircraft.longitude_deg":{"value":-9.1,"valid":true},"aircraft.altitude_m":{"value":12.5,"valid":true},"heading_deg":{"value":270.0,"valid":true}}}})";
  std::string error;
  const std::vector<std::uint8_t> bytes(text.begin(), text.end());
  auto result = dji_edge_transport::decode_telemetry(bytes, 1200, error);
  ASSERT_TRUE(result.has_value()) << error;
  EXPECT_TRUE(result->position_valid);
  EXPECT_DOUBLE_EQ(result->latitude_deg, 38.7);
  EXPECT_DOUBLE_EQ(result->longitude_deg, -9.1);
  EXPECT_DOUBLE_EQ(result->altitude_m, 12.5);
  EXPECT_DOUBLE_EQ(result->heading_deg, 270.0);
}

TEST(ProtocolDecoder, DecodesCanonicalRtkAndGimbalSeparately)
{
  const std::string rtk = R"({"v":1,"type":"rtk","session":"s","stream":"rtk:0","seq":5,"rx_mono_ns":101,"data":{"fields":{"fusion.latitude_deg":{"value":38.7001,"valid":true},"fusion.longitude_deg":{"value":-9.1001,"valid":true},"is_being_used":{"value":true,"valid":true}}}})";
  const std::string gimbal = R"({"v":1,"type":"gimbal","session":"s","stream":"gimbal:0","seq":6,"rx_mono_ns":102,"data":{"fields":{"attitude.pitch_deg":{"value":-45.0,"valid":true}}}})";
  std::string error;
  const std::vector<std::uint8_t> rtk_bytes(rtk.begin(), rtk.end());
  const std::vector<std::uint8_t> gimbal_bytes(gimbal.begin(), gimbal.end());
  const auto rtk_result = dji_edge_transport::decode_telemetry(rtk_bytes, 1200, error);
  ASSERT_TRUE(rtk_result.has_value()) << error;
  EXPECT_TRUE(rtk_result->rtk_valid);
  EXPECT_DOUBLE_EQ(rtk_result->latitude_deg, 38.7001);
  const auto gimbal_result = dji_edge_transport::decode_telemetry(gimbal_bytes, 1200, error);
  ASSERT_TRUE(gimbal_result.has_value()) << error;
  EXPECT_TRUE(gimbal_result->gimbal_pitch_valid);
  EXPECT_EQ(gimbal_result->gimbal_android_mono_ns, 102u);
}

TEST(ProtocolDecoder, RejectsMalformedAndWrongFeedMetadata)
{
  const std::string malformed = R"({"v":1,"type":"flight")";
  const std::string fpv = R"({"v":1,"type":"video_au","session":"s","feed":"fpv","frame_seq":4,"rtp_ssrc":8,"rtp_ts":9,"au_first_byte_rx_mono_ns":10})";
  std::string error;
  const std::vector<std::uint8_t> malformed_bytes(malformed.begin(), malformed.end());
  const std::vector<std::uint8_t> fpv_bytes(fpv.begin(), fpv.end());
  EXPECT_FALSE(dji_edge_transport::decode_telemetry(malformed_bytes, 1200, error).has_value());
  EXPECT_FALSE(dji_edge_transport::decode_frame_metadata(fpv_bytes, 1200, error).has_value());
}

TEST(ProtocolDecoder, DecodesPrimaryOnlySenderHealth)
{
  const std::string text = R"({"v":1,"type":"health","session":"s","stream":"health:0","seq":7,"rx_mono_ns":100,"data":{"primary_video_callbacks":10,"primary_video_callback_drops":1,"primary_rtp_packets":20,"primary_access_units":5,"telemetry_queue_drops":2,"telemetry_socket_errors":3,"video_socket_errors":4}})";
  std::string error;
  const std::vector<std::uint8_t> bytes(text.begin(), text.end());
  const auto health = dji_edge_transport::decode_health(bytes, 1200, error);
  ASSERT_TRUE(health.has_value()) << error;
  EXPECT_EQ(health->sequence, 7u);
  EXPECT_EQ(health->primary_rtp_packets, 20u);
  EXPECT_EQ(health->video_socket_errors, 4u);
  EXPECT_FALSE(dji_edge_transport::decode_telemetry(bytes, 1200, error).has_value());
}

TEST(ProtocolDecoder, RejectsWrongStreamAndIncompleteHealth)
{
  const std::string wrong_stream = R"({"v":1,"type":"flight","session":"s","stream":"flight:1","seq":1,"rx_mono_ns":1,"data":{"fields":{}}})";
  const std::string incomplete_health = R"({"v":1,"type":"health","session":"s","stream":"health:0","seq":1,"rx_mono_ns":1,"data":{}})";
  std::string error;
  const std::vector<std::uint8_t> wrong_stream_bytes(wrong_stream.begin(), wrong_stream.end());
  const std::vector<std::uint8_t> incomplete_health_bytes(incomplete_health.begin(), incomplete_health.end());
  EXPECT_FALSE(dji_edge_transport::decode_telemetry(wrong_stream_bytes, 1200, error).has_value());
  EXPECT_FALSE(dji_edge_transport::decode_health(incomplete_health_bytes, 1200, error).has_value());
}
