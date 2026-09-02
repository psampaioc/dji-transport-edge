import unittest

from dji_edge_receiver.config import ReceiverConfig, VideoStreamConfig
from dji_edge_receiver.dashboard_metrics import dashboard_snapshot, local_ipv4_addresses


def feed(name, packets=0, *, status="running", last=None):
    return {"name": name, "pipeline_status": status, "last_packet_mono_ns": last, "relay_errors": 0,
            "stats": {"packets_received": packets, "sequence_gaps": 0, "duplicates": 0, "out_of_order": 0,
                      "kernel_socket_drops": 0, "estimated_fps": 30.0, "estimated_bitrate_bps": 2_000_000,
                      "decoded_frame_count_available": False, "h264": {"stream_info": {"width": 1280, "height": 720}, "last_idr_age_ms": 10}}}


class DashboardMetricsTest(unittest.TestCase):
    def test_primary_is_independent_from_waiting_secondary(self):
        now = 10_000_000_000
        state = {"clock": {"ready": False, "sample_count": 0}, "video_frames": {}, "sources": {}, "transport": {}}
        health = {"status": "ok", "evidence": {}, "video": [feed("primary", 12, last=now - 100), feed("secondary")]}
        model = dashboard_snapshot(state, health, ReceiverConfig(video_streams=(VideoStreamConfig(),)), now_ns=now)
        self.assertEqual([item["status"] for item in model["feeds"]], ["receiving", "waiting"])

    def test_delay_never_exists_without_ready_clock(self):
        state = {"clock": {"ready": False, "sample_count": 0}, "video_frames": {"primary": {"mapped_edge_mono_ns": 1, "edge_receive_mono_ns": 2}}, "sources": {}, "transport": {}}
        health = {"status": "ok", "evidence": {}, "video": []}
        value = dashboard_snapshot(state, health, ReceiverConfig(), now_ns=3)["timing"]["video"]["primary"]
        self.assertFalse(value["available"])

    def test_local_address_excludes_loopback(self):
        result = local_ipv4_addresses(
            lambda *args: [(None, None, None, None, ("127.0.0.1", 0)), (None, None, None, None, ("192.168.1.239", 0))],
            route_probe=lambda: "127.0.0.1",
        )
        self.assertEqual(result, ["192.168.1.239"])
