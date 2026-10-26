#include "dji_edge_transport/rtp_ingress_tracker.hpp"

#include <algorithm>

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
  if (!has_ssrc_ || snapshot_.ssrc != ssrc) {
    snapshot_.sequence_gap_count += pending_gaps_.count();
    pending_gaps_.reset();
    received_sequences_.reset();
    has_ssrc_ = true;
    ++snapshot_.ssrc_epoch_count;
    last_sequence_ = sequence;
    received_sequences_.set(0);
  } else if (has_sequence_) {
    const auto delta = static_cast<std::uint16_t>(sequence - last_sequence_);
    if (delta == 0) {
      ++snapshot_.duplicate_count;
    } else if (delta < 0x8000) {
      if (sequence < last_sequence_) ++snapshot_.sequence_wrap_count;
      const auto shift = std::min<std::size_t>(delta, pending_gaps_.size());
      const auto aged_gaps = pending_gaps_ << shift;
      snapshot_.sequence_gap_count += pending_gaps_.count() - aged_gaps.count();
      pending_gaps_ = aged_gaps;
      received_sequences_ <<= shift;

      const auto skipped = static_cast<std::size_t>(delta - 1);
      const auto pending = std::min(skipped, pending_gaps_.size() - 1);
      snapshot_.sequence_gap_count += skipped - pending;
      for (std::size_t age = 1; age <= pending; ++age) pending_gaps_.set(age);
      last_sequence_ = sequence;
      received_sequences_.set(0);
    } else {
      const auto age = static_cast<std::uint16_t>(last_sequence_ - sequence);
      if (age < received_sequences_.size()) {
        if (received_sequences_.test(age)) {
          ++snapshot_.duplicate_count;
        } else if (pending_gaps_.test(age)) {
          pending_gaps_.reset(age);
          received_sequences_.set(age);
          ++snapshot_.reordered_count;
          ++snapshot_.late_fill_count;
        } else {
          ++snapshot_.reordered_count;
        }
      } else {
        ++snapshot_.reordered_count;
      }
    }
  } else {
    last_sequence_ = sequence;
    received_sequences_.set(0);
  }
  snapshot_.header_continuity_available = true;
  snapshot_.ssrc = ssrc;
  snapshot_.timestamp = read_u32(bytes + 4);
  has_sequence_ = true;
}

RtpIngressSnapshot RtpIngressTracker::snapshot() const
{
  auto result = snapshot_;
  result.pending_gap_count = pending_gaps_.count();
  return result;
}

}  // namespace dji_edge_transport
