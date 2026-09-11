#pragma once

#include "dji_edge_transport/protocol_decoder.hpp"

#include <cstddef>
#include <deque>
#include <map>
#include <mutex>
#include <optional>

namespace dji_edge_transport {

class SourceTimeState {
public:
  explicit SourceTimeState(std::size_t capacity = 64);
  void add(TelemetryPacket packet);
  std::optional<TelemetryPacket> select_for_frame(std::uint64_t android_complete_mono_ns) const;
  std::optional<TelemetryPacket> latest() const;

private:
  std::optional<TelemetryPacket> compose(std::uint64_t android_mono_ns) const;
  static std::optional<TelemetryPacket> latest_not_after(
    const std::deque<TelemetryPacket> & samples, std::uint64_t android_mono_ns);

  std::size_t capacity_;
  mutable std::mutex mutex_;
  std::map<std::string, std::deque<TelemetryPacket>> history_;
};

}  // namespace dji_edge_transport
