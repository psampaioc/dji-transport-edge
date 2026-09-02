import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from urllib.request import urlopen

from dji_edge_receiver.config import NetworkConfig, ReceiverConfig, StorageConfig, VideoStreamConfig
from dji_edge_receiver.server import EdgeReceiver
from test_protocol import rtp_packet


def free_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not reached before timeout")


class ReceiverIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sink.bind(("127.0.0.1", 0))
        self.sink.settimeout(3)
        pipeline_port = self.sink.getsockname()[1]
        self.telemetry_port = free_udp_port()
        self.metadata_port = free_udp_port()
        self.clock_port = free_udp_port()
        self.rtp_port = free_udp_port()
        config = ReceiverConfig(
            network=NetworkConfig(
                bind_host="127.0.0.1",
                telemetry_port=self.telemetry_port,
                frame_metadata_port=self.metadata_port,
                clock_port=self.clock_port,
                http_host="127.0.0.1",
                http_port=0,
            ),
            storage=StorageConfig(Path(self.temp.name), capture_rtp=True),
            video_streams=(
                VideoStreamConfig(
                    name="primary",
                    input_port=self.rtp_port,
                    pipeline_port=pipeline_port,
                    gstreamer_enabled=False,
                ),
            ),
        )
        self.receiver = EdgeReceiver(config)
        self.receiver.start()
        time.sleep(0.1)

    def tearDown(self):
        self.receiver.stop()
        self.sink.close()
        self.temp.cleanup()

    def send_json(self, port, obj):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(json.dumps(obj, separators=(",", ":")).encode(), ("127.0.0.1", port))
        sock.close()

    def test_end_to_end_telemetry_metadata_rtp_and_http(self):
        self.send_json(
            self.telemetry_port,
            {
                "v": 1,
                "type": "flight",
                "session": "bench/one",
                "stream": "aircraft",
                "seq": 1,
                "rx_mono_ns": 1_000_000,
                "data": {"lat": 38.1, "alt_m": 4.2, "is_flying": False},
            },
        )
        self.send_json(
            self.metadata_port,
            {
                "v": 1,
                "type": "video_au",
                "session": "bench/one",
                "feed": "primary",
                "frame_seq": 1,
                "rtp_ssrc": 0x12345678,
                "rtp_ts": 9000,
                "au_first_byte_rx_mono_ns": 1_000_100,
                "au_complete_rx_mono_ns": 1_000_200,
                "idr": True,
            },
        )
        wait_until(lambda: "primary" in self.receiver.state.snapshot()["video_frames"])
        packet = rtp_packet(sequence=1, timestamp=9000)
        rtp_sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rtp_sender.sendto(packet, ("127.0.0.1", self.rtp_port))
        rtp_sender.close()
        relayed, _ = self.sink.recvfrom(65535)
        self.assertEqual(relayed, packet)

        wait_until(lambda: self.receiver.state.snapshot()["transport"]["rtp_packets"] == 1)
        video_health = self.receiver.runtime_health()["video"][0]
        self.assertEqual(video_health["stats"]["packets_received"], 1)
        self.assertEqual(video_health["stats"]["access_units_observed"], 1)
        self.assertGreater(video_health["stats"]["bytes_received"], 12)
        self.assertGreater(video_health["stats"]["socket_receive_buffer_bytes"], 0)
        self.assertEqual(video_health["stats"]["h264"]["idr_count"], 1)
        self.assertFalse(video_health["stats"]["decoded_frame_count_available"])
        with urlopen(f"http://127.0.0.1:{self.receiver.http.port}/v1/state", timeout=2) as response:
            state = json.load(response)
        self.assertEqual(state["flight"]["data"]["lat"], 38.1)
        self.assertEqual(state["video_frames"]["primary"]["data"]["rtp_ts"], 9000)
        self.assertEqual(state["packet_stats"]["flight"]["complete_samples"], 1)
        association = state["video_frames"]["primary"]["telemetry_associations"]["aircraft"]
        self.assertEqual(association["age_ns"], 100)
        self.assertTrue(association["causal"])
        self.assertEqual(state["receiver_health"]["status"], "ok")

        wait_until(lambda: any(Path(self.temp.name).glob("bench_one*/video-*.rtpbin")))
        session_dirs = [p for p in Path(self.temp.name).iterdir() if p.is_dir() and p.name.startswith("bench_one")]
        self.assertEqual(len(session_dirs), 1)
        session = session_dirs[0]
        self.assertTrue((session / "telemetry.ndjson").is_file())
        self.assertTrue((session / "frame_metadata.ndjson").is_file())
        self.assertGreater((session / next(p.name for p in session.glob("video-*.rtpbin"))).stat().st_size, len(packet))

    def test_invalid_packet_is_rejected_and_recorded(self):
        self.send_json(
            self.telemetry_port,
            {"v": 99, "type": "flight", "session": "s", "stream": "a", "seq": 0, "rx_mono_ns": 1},
        )
        wait_until(lambda: self.receiver.state.snapshot()["transport"]["rejected_packets"] == 1)
        error_file = Path(self.temp.name) / "_receiver" / "protocol_errors.ndjson"
        wait_until(error_file.is_file)

    def test_unknown_video_stream_is_rejected(self):
        self.send_json(
            self.metadata_port,
            {
                "v": 1, "type": "video_au", "session": "s", "feed": "fpv",
                "frame_seq": 1, "rtp_ssrc": 1, "rtp_ts": 90,
                "au_first_byte_rx_mono_ns": 10, "au_complete_rx_mono_ns": 11,
            },
        )
        wait_until(lambda: self.receiver.state.snapshot()["transport"]["rejected_packets"] == 1)
        self.assertNotIn("fpv", self.receiver.state.snapshot()["video_frames"])

    def test_clock_pong_updates_mapping(self):
        now = time.monotonic_ns()
        self.send_json(
            self.clock_port,
            {
                "v": 1,
                "type": "clock_pong",
                "session": "bench-clock",
                "stream": "clock",
                "seq": 1,
                "t0_edge_send_mono_ns": now - 1_000_000,
                "t1_android_rx_mono_ns": now + 4_500_000,
                "t2_android_tx_mono_ns": now + 4_600_000,
            },
        )
        wait_until(lambda: self.receiver.clock.estimate()["ready"])
        estimate = self.receiver.clock.estimate()
        self.assertEqual(estimate["sample_count"], 1)
        self.assertGreater(estimate["offset_android_minus_edge_ns"], 4_000_000)


if __name__ == "__main__":
    unittest.main()
