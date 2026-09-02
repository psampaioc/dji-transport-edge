import unittest

from scripts.capture_transport_bench import summary


def sample(mono_ns, *, packets, gaps, access_units, health_counter):
    return {
        "edge_mono_ns": mono_ns,
        "session": "bench-one",
        "transport": {"rtp_packets": packets, "rtp_sequence_gaps": gaps},
        "receiver_health": {
            "video": [{
                "name": "primary",
                "stats": {
                    "packets_received": packets,
                    "bytes_received": packets * 1000,
                    "packets_rejected": 0,
                    "sequence_gaps": gaps,
                    "duplicates": 0,
                    "out_of_order": 0,
                    "access_units_observed": access_units,
                    "kernel_socket_drops": 0,
                },
            }],
        },
        "health": {"data": {
            "primary_video_callbacks": health_counter,
            "primary_access_units": health_counter,
            "primary_rtp_packets": health_counter * 2,
            "callback_hz": {"flight_controller:0": 5.0},
        }},
        "packet_stats": {"flight": {
            "datagrams": health_counter,
            "bytes": health_counter * 10,
            "complete_samples": health_counter,
        }},
        "video_frames": {},
        "sources": {},
    }


class BenchSummaryTest(unittest.TestCase):
    def test_summary_reports_interval_deltas_and_rates(self):
        report = summary([
            sample(1_000_000_000, packets=100, gaps=2, access_units=10, health_counter=20),
            sample(3_000_000_000, packets=140, gaps=4, access_units=70, health_counter=30),
        ])
        primary = report["video_delta"]["primary"]
        self.assertEqual(report["measurement_duration_s"], 2.0)
        self.assertEqual(primary["packets_received"], 40)
        self.assertEqual(primary["access_units_observed"], 60)
        self.assertEqual(primary["observed_access_unit_fps"], 30.0)
        self.assertEqual(primary["observed_bitrate_bps"], 160_000)
        self.assertEqual(primary["sequence_gap_ratio"], round(2 / 42, 6))
        self.assertEqual(report["sender_delta"]["primary_video_callbacks"], 10)
        self.assertEqual(report["packet_stats_delta"]["flight"]["complete_samples"], 10)


if __name__ == "__main__":
    unittest.main()
