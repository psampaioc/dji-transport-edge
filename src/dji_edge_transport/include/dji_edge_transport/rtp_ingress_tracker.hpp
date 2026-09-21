#pragma once

#include <cstddef>
#include <cstdint>

namespace dji_edge_transport {

struct RtpIngressSnapshot {
  bool header_continuity_available{false};
  std::uint64_t malformed_header_count{0};
  std::uint64_t sequence_gap_count{0};
  std::uint64_t duplicate_count{0};
  std::uint64_t reordered_count{0};
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
  bool has_sequence_{false};
  std::uint16_t last_sequence_{0};
};

}  // namespace dji_edge_transport
