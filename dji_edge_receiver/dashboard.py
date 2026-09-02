"""Local dashboard supervisor for the edge receiver.

It is intentionally a small loopback HTTP surface.  Native GStreamer remains
the only live video presentation path; this module only reads receiver state.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import tempfile
from threading import Event, Lock, Thread
import time
from typing import Callable
import webbrowser

from .bench import build_report, write_report
from .config import ReceiverConfig, load_config
from .dashboard_metrics import dashboard_snapshot
from .server import EdgeReceiver


STATIC_ROOT = Path(__file__).with_name("dashboard_static")
_TABLET_IP = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _validate_ipv4(value: object) -> str:
    if not isinstance(value, str) or not _TABLET_IP.fullmatch(value):
        raise ValueError("tablet IP must be an IPv4 address")
    parts = value.split(".")
    if any(int(part) > 255 for part in parts):
        raise ValueError("tablet IP must be an IPv4 address")
    return value


def patch_android_clock_host(text: str, address: str) -> str:
    """Change only the network setting while retaining comments and other TOML."""
    replacement = f'android_clock_host = "{address}"'
    network = re.search(r"(?ms)^\[network\]\s*$.*?(?=^\[|^\[\[|\Z)", text)
    if network is None:
        raise ValueError("config must contain a [network] table")
    section = network.group(0)
    if re.search(r"(?m)^\s*android_clock_host\s*=.*$", section):
        changed = re.sub(r"(?m)^\s*android_clock_host\s*=.*$", replacement, section, count=1)
    else:
        changed = section.rstrip() + "\n" + replacement + "\n"
    return text[:network.start()] + changed + text[network.end():]


def patch_storage_capture_rtp(text: str, enabled: bool) -> str:
    """Change only the raw RTP evidence policy while retaining other TOML."""
    storage = re.search(r"(?ms)^\[storage\]\s*$.*?(?=^\[|^\[\[|\Z)", text)
    if storage is None:
        return text.rstrip() + f"\n\n[storage]\ncapture_rtp = {'true' if enabled else 'false'}\n"
    section = storage.group(0)
    replacement = f"capture_rtp = {'true' if enabled else 'false'}"
    if re.search(r"(?m)^\s*capture_rtp\s*=.*$", section):
        changed = re.sub(r"(?m)^\s*capture_rtp\s*=.*$", replacement, section, count=1)
    else:
        changed = section.rstrip() + "\n" + replacement + "\n"
    return text[:storage.start()] + changed + text[storage.end():]


class DashboardApplication:
    def __init__(
        self,
        config_path: str | Path,
        *,
        receiver_factory: Callable[[ReceiverConfig], EdgeReceiver] = EdgeReceiver,
        browser_open: Callable[[str], object] = webbrowser.open,
        bench_dir: str | Path | None = None,
        shutdown_request: Callable[[], None] | None = None,
    ) -> None:
        self.config_path = Path(config_path).resolve()
        self.receiver_factory = receiver_factory
        self.browser_open = browser_open
        self.bench_dir = Path(bench_dir) if bench_dir else self.config_path.parent / ".runtime" / "bench"
        self.shutdown_request = shutdown_request
        self.config = load_config(self.config_path)
        self.receiver: EdgeReceiver | None = None
        self.http: ThreadingHTTPServer | None = None
        self.http_thread: Thread | None = None
        self._lock = Lock()
        self._started = False
        self._last_result: dict = {"status": "not_started"}
        self._bench_stop: Event | None = None
        self._bench_thread: Thread | None = None
        self._bench: dict | None = None

    @property
    def port(self) -> int:
        return 0 if self.http is None else int(self.http.server_address[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, *, open_browser: bool | None = None) -> None:
        with self._lock:
            if self._started:
                return
            self.receiver = self.receiver_factory(self.config)
            self.receiver.start()
            try:
                self._start_http()
            except Exception:
                self.receiver.stop()
                self.receiver = None
                raise
            self._started = True
            self._last_result = {"status": "running", "message": "Receiver and dashboard are running."}
        should_open = self.config.dashboard.open_browser if open_browser is None else open_browser
        if should_open:
            try:
                self.browser_open(self.url)
            except Exception as exc:  # Browser availability never stops ingest.
                self._last_result = {"status": "running", "message": f"Open {self.url}; browser launch failed: {exc}"}

    def stop(self) -> None:
        self.stop_bench()
        with self._lock:
            if not self._started:
                return
            if self.http is not None:
                self.http.shutdown()
                self.http.server_close()
            if self.http_thread is not None:
                self.http_thread.join(timeout=2)
            self.http = None
            self.http_thread = None
            if self.receiver is not None:
                self.receiver.stop()
            self.receiver = None
            self._started = False

    def _start_http(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                outer._handle_post(self)

            def log_message(self, format: str, *args) -> None:
                return

        self.http = ThreadingHTTPServer((self.config.dashboard.host, self.config.dashboard.port), Handler)
        self.http_thread = Thread(target=self.http.serve_forever, name="dashboard-http", daemon=True)
        self.http_thread.start()

    def _send_json(self, handler: BaseHTTPRequestHandler, value: dict, status: int = 200) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def _send_static(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        safe = {"/": "index.html", "/index.html": "index.html", "/app.css": "app.css", "/app.js": "app.js"}
        name = safe.get(path)
        if name is None:
            handler.send_error(404)
            return
        payload = (STATIC_ROOT / name).read_bytes()
        content_type = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                        ".js": "application/javascript; charset=utf-8"}[Path(name).suffix]
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.path == "/api/status":
            self._send_json(handler, self.status())
        elif handler.path == "/api/config":
            self._send_json(handler, self.display_config())
        else:
            self._send_static(handler, handler.path)

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        # A loopback port is not an invitation for arbitrary web pages to edit
        # the local receiver. The custom header makes cross-origin form posts
        # and fetches fail before configuration reaches the application.
        if handler.headers.get("X-Edge-Dashboard") != "1":
            self._send_json(handler, {"status": "error", "message": "dashboard request header required"}, 403)
            return
        length = int(handler.headers.get("Content-Length", "0"))
        try:
            body = json.loads(handler.rfile.read(length)) if length else {}
            if not isinstance(body, dict):
                raise ValueError("request body must be a JSON object")
            if handler.path == "/api/config":
                self._send_json(handler, self.apply_settings(
                    body.get("android_clock_host"),
                    body.get("capture_rtp", self.config.storage.capture_rtp),
                ))
            elif handler.path == "/api/bench/start":
                self._send_json(handler, self.start_bench(body.get("duration_s", 60), body.get("interval_s", 1)))
            elif handler.path == "/api/bench/stop":
                self._send_json(handler, self.stop_bench())
            elif handler.path == "/api/shutdown":
                self._send_json(handler, self.request_shutdown())
            else:
                handler.send_error(404)
        except ValueError as exc:
            self._send_json(handler, {"status": "error", "message": str(exc)}, 400)
        except RuntimeError as exc:
            self._send_json(handler, {"status": "conflict", "message": str(exc)}, 409)

    def display_config(self) -> dict:
        return {
            "android_clock_host": self.config.network.android_clock_host,
            "receiver_bind_host": self.config.network.bind_host,
            "http_port": self.config.network.http_port,
            "dashboard_url": self.url,
            "evidence_dir": str(self.config.storage.evidence_dir),
            "capture_rtp": self.config.storage.capture_rtp,
            "video_streams": [asdict(stream) for stream in self.config.video_streams],
        }

    def status(self) -> dict:
        with self._lock:
            receiver = self.receiver
            config = self.config
            result = dict(self._last_result)
        if receiver is None:
            return {"status": "stopped", "result": result, "bench": self._bench}
        model = dashboard_snapshot(receiver.state.snapshot(), receiver.runtime_health(), config)
        model["network"]["dashboard_url"] = self.url
        model["result"] = result
        model["bench"] = self._bench
        return model

    def _write_candidate(self, updated_text: str) -> Path:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=self.config_path.parent,
                                         prefix=f".{self.config_path.name}.", suffix=".tmp") as stream:
            stream.write(updated_text)
            return Path(stream.name)

    def apply_tablet_ip(self, value: object) -> dict:
        """Compatibility helper for callers that only edit the tablet IP."""
        return self.apply_settings(value, self.config.storage.capture_rtp)

    def request_shutdown(self) -> dict:
        """Ask the foreground CLI owner to stop the whole application."""
        with self._lock:
            if not self._started:
                return {"status": "stopped", "message": "Transport is already stopped."}
            self._last_result = {"status": "stopping", "message": "Stopping receiver, dashboard, and GStreamer video windows."}
        if self.shutdown_request is not None:
            self.shutdown_request()
        return dict(self._last_result)

    def apply_settings(self, value: object, capture_rtp: object) -> dict:
        address = _validate_ipv4(value)
        if not isinstance(capture_rtp, bool):
            raise ValueError("RTP capture must be true or false")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("receiver configuration is already being applied")
        try:
            original = self.config_path.read_text(encoding="utf-8")
            updated = patch_android_clock_host(original, address)
            candidate = self._write_candidate(patch_storage_capture_rtp(updated, capture_rtp))
            try:
                new_config = load_config(candidate)
                os.replace(candidate, self.config_path)
            except Exception:
                candidate.unlink(missing_ok=True)
                raise
            old_config = self.config
            old_receiver = self.receiver
            try:
                if old_receiver is not None:
                    old_receiver.stop()
                replacement = self.receiver_factory(new_config)
                replacement.start()
                self.receiver = replacement
                self.config = new_config
                raw_video = "on" if capture_rtp else "off"
                self._last_result = {
                    "status": "restarted",
                    "message": f"Receiver restarted. Tablet IP: {address}; raw RTP capture: {raw_video}.",
                }
                return self._last_result
            except Exception as exc:
                restore = self._write_candidate(original)
                os.replace(restore, self.config_path)
                self.receiver = None
                try:
                    restored = self.receiver_factory(old_config)
                    restored.start()
                    self.receiver = restored
                    self.config = old_config
                except Exception as restore_exc:
                    self._last_result = {"status": "error", "message": f"Restart failed ({exc}); rollback also failed: {restore_exc}"}
                    return self._last_result
                self._last_result = {"status": "error", "message": f"Restart failed; previous receiver restored: {exc}"}
                return self._last_result
        finally:
            self._lock.release()

    def _state_sample(self) -> dict:
        assert self.receiver is not None
        state = self.receiver.state.snapshot()
        state["receiver_health"] = self.receiver.runtime_health()
        return state

    def start_bench(self, duration_s: object, interval_s: object) -> dict:
        try:
            duration, interval = float(duration_s), float(interval_s)
        except (TypeError, ValueError) as exc:
            raise ValueError("duration_s and interval_s must be positive numbers") from exc
        if duration <= 0 or interval <= 0:
            raise ValueError("duration_s and interval_s must be positive numbers")
        with self._lock:
            if self._bench_thread is not None and self._bench_thread.is_alive():
                raise RuntimeError("a bench measurement is already running")
            if self.receiver is None:
                raise RuntimeError("receiver is not running")
            stop = Event()
            self._bench_stop = stop
            self._bench = {"status": "running", "requested_duration_s": duration, "interval_s": interval,
                           "started_utc": datetime.now(timezone.utc).isoformat()}
            self._bench_thread = Thread(target=self._run_bench, args=(duration, interval, stop), name="dashboard-bench", daemon=True)
            self._bench_thread.start()
            return dict(self._bench)

    def _run_bench(self, duration: float, interval: float, stop: Event) -> None:
        samples: list[dict] = []
        deadline = time.monotonic() + duration
        while True:
            try:
                samples.append(self._state_sample())
            except Exception as exc:
                with self._lock:
                    self._bench = {"status": "error", "message": str(exc)}
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0 or stop.wait(min(interval, remaining)):
                break
        if len(samples) == 1:
            # Preserve report semantics: a zero-duration user stop still gets a valid window.
            samples.append(self._state_sample())
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.bench_dir / f"transport-{stamp}.json"
        try:
            self.bench_dir.mkdir(parents=True, exist_ok=True)
            report = build_report(samples, state_url=f"http://{self.config.network.http_host}:{self.config.network.http_port}/v1/state", requested_duration_s=duration)
            write_report(path, report)
            with self._lock:
                self._bench = {"status": "complete" if not stop.is_set() else "stopped", "path": str(path),
                               "summary": report["summary"], "requested_duration_s": duration}
        except Exception as exc:
            with self._lock:
                self._bench = {"status": "error", "message": str(exc)}

    def stop_bench(self) -> dict:
        with self._lock:
            stop = self._bench_stop
            bench = dict(self._bench) if self._bench else {"status": "idle"}
        if stop is not None and not stop.is_set():
            stop.set()
            bench["status"] = "stopping"
        return bench
