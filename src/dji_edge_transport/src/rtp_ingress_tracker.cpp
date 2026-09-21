#include "dji_edge_transport/rtp_ingress_tracker.hpp"

namespace dji_edge_transport {
namespace {
std::uint16_t read_u16(const std::uint8_t * bytes)
{
  return static_cast<std::uint16_t>((bytes[0] << 8) | bytes[1]);
}

std::uint32_t read_u32(const std::uint8_t * bytes)
{
  return (static_cast<std::uint32_t>(bytes[0]) << 24) |
    (static_cast<std::uint32_t>(bytes[1]) << 16) |
    (static_cast<std::uint32_t>(bytes[2]) << 8) | bytes[3];
}
}  // namespace

RtpIngressTracker::RtpIngressTracker(std::uint8_t expected_payload_type)
: expected_payload_type_(expected_payload_type) {}

void RtpIngressTracker::observe(const std::uint8_t * bytes, std::size_t size)
{
  if (!bytes || size < 12 || (bytes[0] >> 6) != 2 || (bytes[1] & 0x7f) != expected_payload_type_) {
    ++snapshot_.malformed_header_count;
    return;
  }
  const std::size_t csrc_bytes = static_cast<std::size_t>(bytes[0] & 0x0f) * 4;
  std::size_t header_size = 12 + csrc_bytes;
  if (header_size > size) {
    ++snapshot_.malformed_header_count;
    return;
  }
  if ((bytes[0] & 0x10) != 0) {
    if (header_size + 4 > size) {
      ++snapshot_.malformed_header_count;
      return;
    }
    const std::size_t extension_bytes = 4 + static_cast<std::size_t>(read_u16(bytes + header_size + 2)) * 4;
    if (header_size + extension_bytes > size) {
      ++snapshot_.malformed_header_count;
      return;
    }
  }
  const auto sequence = read_u16(bytes + 2);
  const auto ssrc = read_u32(bytes + 8);
  if (snapshot_.ssrc != 0 && snapshot_.ssrc != ssrc) has_sequence_ = false;
  if (has_sequence_) {
    const auto delta = static_cast<std::uint16_t>(sequence - last_sequence_);
    if (delta == 0) ++snapshot_.duplicate_count;
    else if (delta < 0x8000) snapshot_.sequence_gap_count += delta - 1;
    else ++snapshot_.reordered_count;
  }
  snapshot_.header_continuity_available = true;
  snapshot_.ssrc = ssrc;
  snapshot_.timestamp = read_u32(bytes + 4);
  last_sequence_ = sequence;
  has_sequence_ = true;
}

RtpIngressSnapshot RtpIngressTracker::snapshot() const { return snapshot_; }

}  // namespace dji_edge_transport
