#include "dji_edge_transport/source_time_state.hpp"

#include <gtest/gtest.h>

TEST(SourceTimeState, SelectsLatestTelemetryNotAfterFrameCompletion)
{
  dji_edge_transport::SourceTimeState state(2);
  dji_edge_transport::TelemetryPacket first;
  first.type = "flight";
  first.android_mono_ns = 10;
  first.sequence = 1;
  first.position_valid = true;
  dji_edge_transport::TelemetryPacket second;
  second.type = "flight";
  second.android_mono_ns = 20;
  second.sequence = 2;
  second.position_valid = true;
  state.add(first);
  state.add(second);
  ASSERT_TRUE(state.select_for_frame(19).has_value());
  EXPECT_EQ(state.select_for_frame(19)->sequence, 1u);
  ASSERT_TRUE(state.select_for_frame(20).has_value());
  EXPECT_EQ(state.select_for_frame(20)->sequence, 2u);
  EXPECT_FALSE(state.select_for_frame(9).has_value());
}

TEST(SourceTimeState, CombinesCausalFlightRtkAndGimbalSamples)
{
  dji_edge_transport::SourceTimeState state(4);
  dji_edge_transport::TelemetryPacket flight;
  flight.type = "flight";
  flight.session = "s";
  flight.android_mono_ns = 10;
  flight.position_valid = true;
  flight.latitude_deg = 1.0;
  flight.longitude_deg = 2.0;
  flight.altitude_m = 3.0;
  flight.heading_deg = 4.0;
  dji_edge_transport::TelemetryPacket rtk;
  rtk.type = "rtk";
  rtk.session = "s";
  rtk.android_mono_ns = 11;
  rtk.rtk_valid = true;
  rtk.latitude_deg = 10.0;
  rtk.longitude_deg = 20.0;
  dji_edge_transport::TelemetryPacket gimbal;
  gimbal.type = "gimbal";
  gimbal.session = "s";
  gimbal.android_mono_ns = 12;
  gimbal.gimbal_pitch_valid = true;
  gimbal.gimbal_pitch_deg = -45.0;
  state.add(flight);
  state.add(rtk);
  state.add(gimbal);

  const auto result = state.select_for_frame(12);
  ASSERT_TRUE(result.has_value());
  EXPECT_TRUE(result->rtk_valid);
  EXPECT_DOUBLE_EQ(result->latitude_deg, 10.0);
  EXPECT_DOUBLE_EQ(result->longitude_deg, 20.0);
  EXPECT_DOUBLE_EQ(result->altitude_m, 3.0);
  EXPECT_DOUBLE_EQ(result->heading_deg, 4.0);
  EXPECT_TRUE(result->gimbal_pitch_valid);
  EXPECT_DOUBLE_EQ(result->gimbal_pitch_deg, -45.0);
}

TEST(SourceTimeState, DoesNotRegressOrLetRtkEraseFlightAltitudeAndHeading)
{
  dji_edge_transport::SourceTimeState state(4);
  dji_edge_transport::TelemetryPacket flight;
  flight.type = "flight";
  flight.android_mono_ns = 20;
  flight.sequence = 2;
  flight.position_valid = true;
  flight.latitude_deg = 1.0;
  flight.longitude_deg = 2.0;
  flight.altitude_m = 3.0;
  flight.heading_deg = 4.0;
  dji_edge_transport::TelemetryPacket stale = flight;
  stale.android_mono_ns = 10;
  stale.sequence = 1;
  stale.altitude_m = 100.0;
  dji_edge_transport::TelemetryPacket unusable_rtk;
  unusable_rtk.type = "rtk";
  unusable_rtk.android_mono_ns = 21;
  unusable_rtk.rtk_valid = false;
  state.add(flight);
  state.add(stale);
  state.add(unusable_rtk);
  const auto result = state.latest();
  ASSERT_TRUE(result.has_value());
  EXPECT_EQ(result->sequence, 2u);
  EXPECT_DOUBLE_EQ(result->altitude_m, 3.0);
  EXPECT_DOUBLE_EQ(result->heading_deg, 4.0);
  EXPECT_FALSE(result->rtk_valid);
}

TEST(SourceTimeState, ResetPreventsOldSessionComposition)
{
  dji_edge_transport::SourceTimeState state(4);
  dji_edge_transport::TelemetryPacket flight_a;
  flight_a.type = "flight";
  flight_a.session = "a";
  flight_a.android_mono_ns = 10;
  flight_a.position_valid = true;
  dji_edge_transport::TelemetryPacket rtk_a;
  rtk_a.type = "rtk";
  rtk_a.session = "a";
  rtk_a.android_mono_ns = 11;
  rtk_a.rtk_valid = true;
  state.add(flight_a);
  state.add(rtk_a);
  state.reset("b");
  state.add(rtk_a);
  EXPECT_FALSE(state.latest().has_value());
  dji_edge_transport::TelemetryPacket flight_b = flight_a;
  flight_b.session = "b";
  flight_b.android_mono_ns = 20;
  state.add(flight_b);
  ASSERT_TRUE(state.latest().has_value());
  EXPECT_FALSE(state.latest()->rtk_valid);
}
