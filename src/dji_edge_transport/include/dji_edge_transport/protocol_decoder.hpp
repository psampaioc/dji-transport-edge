#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace dji_edge_transport {

struct TelemetryPacket {
  std::string type;
  std::string session;
  std::uint64_t sequence{0};
  std::uint64_t android_mono_ns{0};
  double latitude_deg{0.0};
  double longitude_deg{0.0};
  double altitude_m{0.0};
  double heading_deg{0.0};
  double gimbal_pitch_deg{0.0};
  std::uint64_t gimbal_android_mono_ns{0};
  bool position_valid{false};
  bool rtk_valid{false};
  bool gimbal_pitch_valid{false};
};

struct FrameMetadata {
  std::string session;
  std::string feed;
  std::uint64_t frame_seq{0};
  std::uint32_t rtp_ssrc{0};
  std::uint32_t rtp_ts{0};
  std::uint64_t first_byte_mono_ns{0};
  std::uint64_t complete_mono_ns{0};
  std::optional<std::uint64_t> dji_source_timestamp_ns;
  std::string dji_timestamp_source;
};

struct ClockPacket {
  std::uint64_t t0_edge_send_mono_ns{0};
  std::uint64_t t1_android_rx_mono_ns{0};
  std::uint64_t t2_android_tx_mono_ns{0};
  bool is_pong{false};
};

std::optional<TelemetryPacket> decode_telemetry(
  const std::vector<std::uint8_t> & bytes, std::size_t max_bytes, std::string & error);
std::optional<FrameMetadata> decode_frame_metadata(
  const std::vector<std::uint8_t> & bytes, std::size_t max_bytes, std::string & error);
std::optional<ClockPacket> decode_clock(
  const std::vector<std::uint8_t> & bytes, std::size_t max_bytes, std::string & error);

}  // namespace dji_edge_transport
