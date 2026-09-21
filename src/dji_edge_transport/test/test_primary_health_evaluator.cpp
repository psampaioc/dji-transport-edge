#include "dji_edge_transport/primary_health_evaluator.hpp"

#include <gtest/gtest.h>

namespace {

TEST(PrimaryHealthEvaluator, SeparatesObservablePrimaryBoundaries)
{
  dji_edge_transport::PrimaryHealthEvaluator evaluator(1.0, 2.0);
  using State = dji_edge_transport::PrimaryHealthState;
  dji_edge_transport::PrimaryHealthInput input;

  EXPECT_EQ(evaluator.evaluate(input).state, State::waiting_for_udp);
  input.ingress_seen = true;
  input.ingress_age_s = 0.1;
  EXPECT_EQ(evaluator.evaluate(input).state, State::waiting_for_keyframe);
  input.decoded_age_s = 1.1;
  EXPECT_EQ(evaluator.evaluate(input).state, State::decoding);
  input.decoded_age_s = 0.1;
  input.published_age_s = 1.1;
  EXPECT_EQ(evaluator.evaluate(input).state, State::delivery_stalled);
  input.published_age_s = 0.1;
  EXPECT_EQ(evaluator.evaluate(input).state, State::publishing);
  input.pipeline_error = "pipeline failed";
  EXPECT_EQ(evaluator.evaluate(input).state, State::pipeline_error);
}

TEST(PrimaryHealthEvaluator, QualifiesButDoesNotProveSenderToIngressGap)
{
  dji_edge_transport::PrimaryHealthEvaluator evaluator(1.0, 2.0);
  dji_edge_transport::PrimaryHealthInput health_input;
  health_input.ingress_seen = true;
  health_input.ingress_age_s = 1.1;
  health_input.decoded_age_s = 0.1;
  health_input.published_age_s = 0.1;
  health_input.sender_health_age_s = 0.5;
  const auto health_gap = evaluator.evaluate(health_input);
  EXPECT_EQ(health_gap.state, dji_edge_transport::PrimaryHealthState::waiting_for_udp);
  EXPECT_NE(health_gap.reason.find("qualified"), std::string::npos);

  health_input.sender_health_age_s = 3.0;
  health_input.metadata_age_s = 3.0;
  const auto unknown_gap = evaluator.evaluate(health_input);
  EXPECT_EQ(unknown_gap.state, dji_edge_transport::PrimaryHealthState::waiting_for_udp);
  EXPECT_EQ(unknown_gap.reason, "Primary RTP ingress is stale");
}

}  // namespace
