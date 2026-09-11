#pragma once

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <optional>
#include <vector>

namespace dji_edge_transport {

struct ClockEstimate {
  bool ready{false};
  std::size_t sample_count{0};
  std::uint64_t best_rtt_ns{0};
  double offset_android_minus_edge_ns{0.0};
};

class ClockMapper {
public:
  explicit ClockMapper(std::size_t max_samples = 64, std::size_t best_sample_count = 8);
  bool add_exchange(
    std::uint64_t t0_edge_send_ns, std::uint64_t t1_android_receive_ns,
    std::uint64_t t2_android_send_ns, std::uint64_t t3_edge_receive_ns);
  ClockEstimate estimate() const;
  std::optional<std::uint64_t> android_to_edge(std::uint64_t android_mono_ns) const;

private:
  struct Sample {
    std::uint64_t rtt_ns;
    double offset_android_minus_edge_ns;
  };
  std::size_t max_samples_;
  std::size_t best_sample_count_;
  mutable std::mutex mutex_;
  std::vector<Sample> samples_;
};

}  // namespace dji_edge_transport
