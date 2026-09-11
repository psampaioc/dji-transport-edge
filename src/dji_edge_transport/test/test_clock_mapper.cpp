#include "dji_edge_transport/clock_mapper.hpp"

#include <gtest/gtest.h>

TEST(ClockMapper, ComputesNtpStyleOffsetAndKeepsBestRttSamples)
{
  dji_edge_transport::ClockMapper mapper(4, 2);
  ASSERT_TRUE(mapper.add_exchange(100, 160, 180, 240));
  ASSERT_TRUE(mapper.add_exchange(1'000, 1'040, 1'050, 1'090));
  ASSERT_TRUE(mapper.add_exchange(2'000, 2'100, 2'110, 2'220));

  const auto estimate = mapper.estimate();
  EXPECT_TRUE(estimate.ready);
  EXPECT_EQ(estimate.sample_count, 3u);
  EXPECT_EQ(estimate.best_rtt_ns, 80u);
  EXPECT_DOUBLE_EQ(estimate.offset_android_minus_edge_ns, 0.0);
  EXPECT_EQ(mapper.android_to_edge(1'000), 1'000u);
}

TEST(ClockMapper, RejectsInvalidExchangeWithoutChangingEstimate)
{
  dji_edge_transport::ClockMapper mapper;
  EXPECT_FALSE(mapper.add_exchange(100, 90, 80, 110));
  EXPECT_FALSE(mapper.estimate().ready);
}
