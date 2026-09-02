import json
from pathlib import Path
import socket
import tempfile
import unittest
from urllib.request import Request, urlopen

from dji_edge_receiver.dashboard import (
    DashboardApplication,
    patch_android_clock_host,
    patch_storage_capture_rtp,
)
from dji_edge_receiver.server import EdgeReceiver


def free_udp_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class FakeState:
    def snapshot(self):
        return {"clock": {"ready": False, "sample_count": 0}, "video_frames": {}, "sources": {}, "transport": {}, "flight": None, "rtk": None, "gimbals": [], "health": None}


class FakeReceiver:
    created = []
    def __init__(self, config):
        self.config = config
        self.state = FakeState()
        self.started = False
        FakeReceiver.created.append(self)
    def start(self): self.started = True
    def stop(self): self.started = False
    def runtime_health(self):
        return {"status": "ok", "evidence": {"queue_depth": 0, "dropped_records": 0, "write_errors": 0, "writer_alive": True}, "video": [{"name": "primary", "pipeline_status": "disabled", "last_packet_mono_ns": None, "relay_errors": 0, "stats": {}}, {"name": "secondary", "pipeline_status": "disabled", "last_packet_mono_ns": None, "relay_errors": 0, "stats": {}}]}


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.toml"
        self.path.write_text("[network]\nandroid_clock_host = '192.168.1.151'\n[dashboard]\nport = 0\nopen_browser = false\n", encoding="utf-8")
        self.shutdown_requested = False
        self.app = DashboardApplication(
            self.path, receiver_factory=FakeReceiver, browser_open=lambda url: None,
            bench_dir=Path(self.temp.name) / "bench", shutdown_request=self._request_shutdown,
        )
        self.app.start(open_browser=False)

    def _request_shutdown(self):
        self.shutdown_requested = True

    def tearDown(self):
        self.app.stop()
        self.temp.cleanup()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if data:
            headers["X-Edge-Dashboard"] = "1"
        request = Request(self.app.url + path, data=data, headers=headers, method="POST" if data else "GET")
        with urlopen(request, timeout=2) as response:
            return response.status, response.headers.get_content_type(), json.loads(response.read()) if path.startswith("/api/") else response.read()

    def test_serves_dashboard_and_status(self):
        status, content_type, body = self.request("/api/status")
        self.assertEqual((status, content_type), (200, "application/json"))
        self.assertEqual(body["feeds"][0]["name"], "primary")
        status, content_type, html = self.request("/")
        self.assertEqual((status, content_type), (200, "text/html"))
        self.assertIn(b"DJI Transport Edge", html)
        self.assertIn(b"GSTREAMER VIDEO WINDOWS", html)
        self.assertIn(b"Raw RTP evidence", html)
        self.assertIn(b"Exit transport", html)

    def test_exit_requests_foreground_shutdown(self):
        status, _, result = self.request("/api/shutdown", {})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "stopping")
        self.assertTrue(self.shutdown_requested)

    def test_apply_restarts_only_after_valid_ip(self):
        status, _, result = self.request("/api/config", {"android_clock_host": "192.168.1.77"})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "restarted")
        self.assertIn('android_clock_host = "192.168.1.77"', self.path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(FakeReceiver.created), 2)

    def test_patch_retains_unrelated_toml(self):
        changed = patch_android_clock_host("# keep\n[network]\nhttp_port = 8088\n[storage]\nfsync = false\n", "10.0.0.2")
        self.assertIn("# keep", changed)
        self.assertIn("http_port = 8088", changed)
        self.assertIn('android_clock_host = "10.0.0.2"', changed)

    def test_capture_policy_is_persisted_with_restart(self):
        status, _, result = self.request("/api/config", {"android_clock_host": "192.168.1.77", "capture_rtp": False})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "restarted")
        self.assertIn("capture_rtp = false", self.path.read_text(encoding="utf-8"))
        changed = patch_storage_capture_rtp("[storage]\nfsync = false\n", False)
        self.assertIn("fsync = false", changed)
        self.assertIn("capture_rtp = false", changed)

    def test_actual_receiver_runs_behind_dashboard_without_gstreamer(self):
        ports = [free_udp_port() for _ in range(4)]
        actual = Path(self.temp.name) / "actual.toml"
        actual.write_text(
            "[network]\n"
            "bind_host = '127.0.0.1'\n"
            f"telemetry_port = {ports[0]}\nframe_metadata_port = {ports[1]}\nclock_port = {ports[2]}\nhttp_port = 0\n"
            "[storage]\nevidence_dir = './actual-evidence'\n"
            "[dashboard]\nport = 0\nopen_browser = false\n"
            "[[video_streams]]\nname = 'primary'\n"
            f"input_port = {ports[3]}\npipeline_port = {free_udp_port()}\ngstreamer_enabled = false\n",
            encoding="utf-8",
        )
        app = DashboardApplication(actual, receiver_factory=EdgeReceiver, browser_open=lambda url: None)
        app.start(open_browser=False)
        try:
            with urlopen(app.url + "/api/status", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)["feeds"][0]["status"], "disabled")
        finally:
            app.stop()
