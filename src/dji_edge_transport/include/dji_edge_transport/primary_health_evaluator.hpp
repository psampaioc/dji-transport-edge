#pragma once

#include <cstdint>
#include <string>

namespace dji_edge_transport {

enum class PrimaryHealthState : std::uint8_t {
  waiting_for_udp,
  waiting_for_keyframe,
  decoding,
  publishing,
  pipeline_error,
  delivery_stalled,
};

struct PrimaryHealthInput {
  bool ingress_seen{false};
  double ingress_age_s{-1.0};
  double decoded_age_s{-1.0};
  double published_age_s{-1.0};
  double sender_health_age_s{-1.0};
  double metadata_age_s{-1.0};
  std::string pipeline_error;
};

struct PrimaryHealthAssessment {
  PrimaryHealthState state{PrimaryHealthState::waiting_for_udp};
  std::string reason;
};

class PrimaryHealthEvaluator {
public:
  PrimaryHealthEvaluator(double stale_after_s, double sender_correlation_s);
  PrimaryHealthAssessment evaluate(const PrimaryHealthInput & input) const;

private:
  double stale_after_s_;
  double sender_correlation_s_;
};

}  // namespace dji_edge_transport
