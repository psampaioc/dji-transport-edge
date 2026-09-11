#include "dji_edge_transport/clock_mapper.hpp"

#include <algorithm>

namespace dji_edge_transport {

ClockMapper::ClockMapper(std::size_t max_samples, std::size_t best_sample_count)
: max_samples_(max_samples), best_sample_count_(best_sample_count) {}

bool ClockMapper::add_exchange(
  std::uint64_t t0, std::uint64_t t1, std::uint64_t t2, std::uint64_t t3)
{
  if (t0 == 0 || t1 == 0 || t2 == 0 || t3 == 0 || t3 < t0 || t2 < t1) return false;
  const auto android_processing_ns = t2 - t1;
  const auto edge_elapsed_ns = t3 - t0;
  if (edge_elapsed_ns < android_processing_ns) return false;
  Sample sample{
    edge_elapsed_ns - android_processing_ns,
    (static_cast<double>(t1) - static_cast<double>(t0) +
    static_cast<double>(t2) - static_cast<double>(t3)) / 2.0};
  std::lock_guard<std::mutex> lock(mutex_);
  samples_.push_back(sample);
  if (samples_.size() > max_samples_) samples_.erase(samples_.begin());
  return true;
}

ClockEstimate ClockMapper::estimate() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  if (samples_.empty()) return {};
  auto best = samples_;
  std::sort(best.begin(), best.end(), [](const auto & left, const auto & right) {
    return left.rtt_ns < right.rtt_ns;
  });
  const auto count = std::min(best_sample_count_, best.size());
  double offset_sum = 0.0;
  for (std::size_t index = 0; index < count; ++index) offset_sum += best[index].offset_android_minus_edge_ns;
  return {true, samples_.size(), best.front().rtt_ns, offset_sum / static_cast<double>(count)};
}

std::optional<std::uint64_t> ClockMapper::android_to_edge(std::uint64_t android_mono_ns) const
{
  const auto current = estimate();
  if (!current.ready) return std::nullopt;
  const auto edge = static_cast<double>(android_mono_ns) - current.offset_android_minus_edge_ns;
  if (edge < 0.0) return std::nullopt;
  return static_cast<std::uint64_t>(edge);
}

}  // namespace dji_edge_transport
