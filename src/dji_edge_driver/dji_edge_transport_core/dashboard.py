"""Dependency-free local measurement dashboard for the direct ROS driver."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Callable


_PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DJI Transport Edge</title><style>body{margin:0;background:#0b1018;color:#e9edf5;font:14px system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:32px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #253044;padding-bottom:20px}h1{font-size:24px;margin:0}.sub{color:#91a0b7}button{border:0;border-radius:8px;padding:9px 14px;background:#d75757;color:#fff;cursor:pointer}pre{white-space:pre-wrap;word-break:break-word;background:#121a27;border:1px solid #253044;border-radius:12px;padding:20px;line-height:1.5}</style></head><body><main><header><div><h1>DJI Transport Edge</h1><div class='sub'>Direct Android to ROS 2 transport measurements</div></div><button id='exit'>Exit</button></header><pre id='state'>Loading…</pre></main><script>const state=document.querySelector('#state');async function refresh(){try{state.textContent=JSON.stringify(await (await fetch('/v1/state')).json(),null,2)}catch(e){state.textContent='Dashboard connection lost: '+e}}document.querySelector('#exit').onclick=async()=>{await fetch('/v1/exit',{method:'POST'});state.textContent='Stopping…'};refresh();setInterval(refresh,1000);</script></body></html>"""


class DashboardServer:
    """Small loopback HTTP server owned by the driver process."""

    def __init__(self, host: str, port: int, state_provider: Callable[[], dict[str, Any]], stop_callback: Callable[[], None]) -> None:
        self._state_provider = state_provider
        self._stop_callback = stop_callback

        parent = self
        class Handler(BaseHTTPRequestHandler):
            def _json(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self) -> None:
                if self.path == "/":
                    body = _PAGE.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
                if self.path in {"/v1/state", "/health"}:
                    state = parent._state_provider()
                    self._json(state if self.path == "/v1/state" else {"status": state.get("status", "unknown")})
                    return
                self.send_error(404)
            def do_POST(self) -> None:
                if self.path != "/v1/exit": self.send_error(404); return
                parent._stop_callback(); self._json({"stopping": True})
            def log_message(self, *_: object) -> None: pass

        self._server = ThreadingHTTPServer((host, port), Handler)
        self.port = self._server.server_port
        self._thread = Thread(target=self._server.serve_forever, name="dji-edge-dashboard", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)
