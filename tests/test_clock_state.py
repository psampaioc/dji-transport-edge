import unittest

from dji_edge_receiver.clock import ClockMapper
from dji_edge_receiver.protocol import Packet
from dji_edge_receiver.state import LatestState, SequenceResult, SequenceTracker


class ClockAndStateTest(unittest.TestCase):
    def test_clock_mapping(self):
        mapper = ClockMapper()
        # Android clock is exactly 1,000 ns ahead; symmetric 100 ns each way.
        sample = mapper.add_exchange(10_000, 11_100, 11_200, 10_300)
        self.assertEqual(sample.round_trip_ns, 200)
        self.assertEqual(sample.offset_android_minus_edge_ns, 1000)
        self.assertEqual(mapper.android_to_edge(21_000), 20_000)

    def test_clock_rejects_invalid_exchange(self):
        with self.assertRaises(ValueError):
            ClockMapper().add_exchange(10, 20, 19, 30)

    def test_sequence_tracking_and_wrap(self):
        tracker = SequenceTracker()
        self.assertEqual(tracker.observe(("s",), 4).disposition, "first")
        result = tracker.observe(("s",), 7)
        self.assertEqual((result.disposition, result.gap), ("gap", 2))
        self.assertEqual(tracker.observe(("s",), 7).disposition, "duplicate")
        self.assertEqual(tracker.observe(("s",), 6).disposition, "out_of_order")

        wrapped = SequenceTracker(modulus=1 << 16)
        wrapped.observe((1,), 65535)
        self.assertEqual(wrapped.observe((1,), 0).disposition, "ok")

    def test_latest_state_does_not_replace_with_duplicate(self):
        state = LatestState(ClockMapper())
        first = Packet(1, "flight", "s", "aircraft", 1, 100, {"alt_m": 1}, {})
        duplicate = Packet(1, "flight", "s", "aircraft", 1, 101, {"alt_m": 99}, {})
        state.update_packet(first, 1000, ("127.0.0.1", 1), SequenceResult("first"))
        state.update_packet(duplicate, 1001, ("127.0.0.1", 1), SequenceResult("duplicate"))
        snapshot = state.snapshot()
        self.assertEqual(snapshot["flight"]["data"]["alt_m"], 1)
        self.assertEqual(snapshot["transport"]["duplicates"], 1)

    def test_latest_state_publishes_only_complete_fragmented_sample(self):
        state = LatestState(ClockMapper())
        common = {"sample_sequence": 8, "component_index": 0, "chunk_count": 2}
        first = Packet(1, "rtk", "s", "rtk:0", 10, 100,
                       {**common, "chunk_index": 0, "fields": {"lat": {"value": 38.0, "valid": True}}}, {})
        second = Packet(1, "rtk", "s", "rtk:0", 11, 100,
                        {**common, "chunk_index": 1, "fields": {"solution": {"value": "FIXED", "valid": True}}}, {})
        state.update_packet(first, 1000, ("127.0.0.1", 1), SequenceResult("first"))
        self.assertIsNone(state.snapshot()["rtk"])
        state.update_packet(second, 1001, ("127.0.0.1", 1), SequenceResult("ok"))
        record = state.snapshot()["rtk"]
        self.assertEqual(record["data"]["sample_sequence"], 8)
        self.assertEqual(record["data"]["fields"]["lat"]["value"], 38.0)
        self.assertEqual(record["data"]["fields"]["solution"]["value"], "FIXED")

    def test_frame_association_retains_previous_telemetry_after_newer_update(self):
        state = LatestState(ClockMapper())
        old = Packet(1, "flight", "s", "aircraft", 1, 100, {"alt_m": 1}, {})
        newer = Packet(1, "flight", "s", "aircraft", 2, 300, {"alt_m": 2}, {})
        frame = Packet(1, "video_au", "s", "primary", 1, 200,
                       {"rtp_ssrc": 1, "rtp_ts": 2}, {})
        state.update_packet(old, 1_000, ("127.0.0.1", 1), SequenceResult("first"))
        state.update_packet(newer, 1_001, ("127.0.0.1", 1), SequenceResult("ok"))
        state.update_packet(frame, 1_002, ("127.0.0.1", 1), SequenceResult("first"))
        association = state.snapshot()["video_frames"]["primary"]["telemetry_associations"]["aircraft"]
        self.assertEqual(association["sequence"], 1)
        self.assertEqual(association["age_ns"], 100)
        self.assertTrue(association["causal"])


if __name__ == "__main__":
    unittest.main()
