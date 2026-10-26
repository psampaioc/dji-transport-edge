#include "dji_edge_transport/primary_health_evaluator.hpp"

#include <algorithm>

namespace dji_edge_transport {

PrimaryHealthEvaluator::PrimaryHealthEvaluator(double stale_after_s, double sender_correlation_s)
: stale_after_s_(std::max(stale_after_s, 0.01)),
  sender_correlation_s_(std::max(sender_correlation_s, stale_after_s_)) {}

PrimaryHealthAssessment PrimaryHealthEvaluator::evaluate(const PrimaryHealthInput & input) const
{
  if (!input.pipeline_error.empty()) {
    return {PrimaryHealthState::pipeline_error, input.pipeline_error};
  }
  if (!input.ingress_seen) {
    return {PrimaryHealthState::waiting_for_udp, "no Primary RTP observed"};
  }
  if (input.ingress_age_s > stale_after_s_) {
    if (input.sender_health_age_s >= 0.0 && input.sender_health_age_s < sender_correlation_s_) {
      return {PrimaryHealthState::waiting_for_udp,
        "qualified Android-local progress / Edge RTP ingress gap; network cause indeterminate"};
    }
    if (input.metadata_age_s >= 0.0 && input.metadata_age_s < sender_correlation_s_) {
      return {PrimaryHealthState::waiting_for_udp,
        "recent Android Primary metadata but stale Edge RTP ingress; network cause indeterminate"};
    }
    return {PrimaryHealthState::waiting_for_udp, "Primary RTP ingress is stale"};
  }
  if (input.decoded_age_s < 0.0) {
    return {PrimaryHealthState::waiting_for_keyframe,
      "RTP observed; waiting for a decodable keyframe"};
  }
  if (input.decoded_age_s > stale_after_s_) {
    return {PrimaryHealthState::decoding,
      "fresh Primary RTP observed; decoder is awaiting a recoverable H.264/IDR"};
  }
  if (input.published_age_s < 0.0 || input.published_age_s > stale_after_s_) {
    return {PrimaryHealthState::delivery_stalled,
      "decoded Primary image is not reaching ROS publication"};
  }
  return {PrimaryHealthState::publishing, "Primary pipeline active"};
}

}  // namespace dji_edge_transport
