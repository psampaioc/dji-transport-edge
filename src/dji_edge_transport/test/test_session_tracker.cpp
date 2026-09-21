#include "dji_edge_transport/session_tracker.hpp"

#include <gtest/gtest.h>

TEST(SessionTracker, RejectsNonMonotonicAndRetiredSessionPackets)
{
  dji_edge_transport::SessionTracker tracker(2);
  EXPECT_EQ(tracker.observe("a", "flight:0", 1), dji_edge_transport::SessionObservation::transitioned);
  EXPECT_EQ(tracker.observe("a", "flight:0", 2), dji_edge_transport::SessionObservation::accepted);
  EXPECT_EQ(tracker.observe("a", "flight:0", 2), dji_edge_transport::SessionObservation::rejected_sequence);
  EXPECT_EQ(tracker.observe("b", "health:0", 1), dji_edge_transport::SessionObservation::transitioned);
  EXPECT_EQ(tracker.observe("a", "flight:0", 3), dji_edge_transport::SessionObservation::rejected_retired);
  EXPECT_EQ(tracker.active_session(), "b");
}

TEST(SessionTracker, TracksSequenceIndependentlyPerLogicalStream)
{
  dji_edge_transport::SessionTracker tracker;
  EXPECT_EQ(tracker.observe("a", "flight:0", 1), dji_edge_transport::SessionObservation::transitioned);
  EXPECT_EQ(tracker.observe("a", "health:0", 1), dji_edge_transport::SessionObservation::accepted);
  EXPECT_EQ(tracker.observe("a", "video_au:primary", 1), dji_edge_transport::SessionObservation::accepted);
}
