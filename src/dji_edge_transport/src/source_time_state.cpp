#include "dji_edge_transport/source_time_state.hpp"

namespace dji_edge_transport {

SourceTimeState::SourceTimeState(std::size_t capacity) : capacity_(capacity) {}

void SourceTimeState::add(TelemetryPacket packet)
{
  if (packet.type != "flight" && packet.type != "rtk" && packet.type != "gimbal") return;
  std::lock_guard<std::mutex> lock(mutex_);
  auto & samples = history_[packet.type];
  if (!samples.empty() && packet.android_mono_ns <= samples.back().android_mono_ns) return;
  samples.push_back(std::move(packet));
  while (samples.size() > capacity_) samples.pop_front();
}

std::optional<TelemetryPacket> SourceTimeState::select_for_frame(
  std::uint64_t android_complete_mono_ns) const
{
  std::lock_guard<std::mutex> lock(mutex_);
  return compose(android_complete_mono_ns);
}

std::optional<TelemetryPacket> SourceTimeState::latest() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  std::uint64_t newest = 0;
  for (const auto & [_, samples] : history_) {
    if (!samples.empty()) newest = std::max(newest, samples.back().android_mono_ns);
  }
  return newest == 0 ? std::nullopt : compose(newest);
}

std::optional<TelemetryPacket> SourceTimeState::latest_not_after(
  const std::deque<TelemetryPacket> & samples, std::uint64_t android_mono_ns)
{
  for (auto it = samples.rbegin(); it != samples.rend(); ++it) {
    if (it->android_mono_ns <= android_mono_ns) return *it;
  }
  return std::nullopt;
}

std::optional<TelemetryPacket> SourceTimeState::compose(std::uint64_t android_mono_ns) const
{
  const auto flight_it = history_.find("flight");
  if (flight_it == history_.end()) return std::nullopt;
  const auto flight = latest_not_after(flight_it->second, android_mono_ns);
  if (!flight || !flight->position_valid) return std::nullopt;

  TelemetryPacket result = *flight;
  const auto rtk_it = history_.find("rtk");
  if (rtk_it != history_.end()) {
    const auto rtk = latest_not_after(rtk_it->second, android_mono_ns);
    if (rtk && rtk->rtk_valid) {
      result.latitude_deg = rtk->latitude_deg;
      result.longitude_deg = rtk->longitude_deg;
      result.rtk_valid = true;
    }
  }
  const auto gimbal_it = history_.find("gimbal");
  if (gimbal_it != history_.end()) {
    const auto gimbal = latest_not_after(gimbal_it->second, android_mono_ns);
    if (gimbal && gimbal->gimbal_pitch_valid) {
      result.gimbal_pitch_valid = true;
      result.gimbal_pitch_deg = gimbal->gimbal_pitch_deg;
      result.gimbal_android_mono_ns = gimbal->gimbal_android_mono_ns;
    }
  }
  return result;
}

}  // namespace dji_edge_transport
