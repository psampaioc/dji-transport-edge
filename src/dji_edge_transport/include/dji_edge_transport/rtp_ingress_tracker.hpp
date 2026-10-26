#pragma once

#include <bitset>
#include <cstddef>
#include <cstdint>

namespace dji_edge_transport {

struct RtpIngressSnapshot {
  static constexpr std::size_t reorder_horizon_packets{64};
  bool header_continuity_available{false};
  std::uint64_t malformed_header_count{0};
  std::uint64_t sequence_gap_count{0};
  std::uint64_t duplicate_count{0};
  std::uint64_t reordered_count{0};
  std::uint64_t pending_gap_count{0};
  std::uint64_t late_fill_count{0};
  std::uint64_t sequence_wrap_count{0};
  std::uint64_t ssrc_epoch_count{0};
  std::uint32_t ssrc{0};
  std::uint32_t timestamp{0};
};

class RtpIngressTracker {
public:
  explicit RtpIngressTracker(std::uint8_t expected_payload_type);
  void observe(const std::uint8_t * bytes, std::size_t size);
  RtpIngressSnapshot snapshot() const;

private:
  std::uint8_t expected_payload_type_;
  RtpIngressSnapshot snapshot_;
  std::bitset<RtpIngressSnapshot::reorder_horizon_packets> pending_gaps_;
  std::bitset<RtpIngressSnapshot::reorder_horizon_packets> received_sequences_;
  bool has_ssrc_{false};
  bool has_sequence_{false};
  std::uint16_t last_sequence_{0};
};

}  // namespace dji_edge_transport
