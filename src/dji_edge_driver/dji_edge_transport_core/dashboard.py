"""Dependency-free local measurement dashboard for the direct ROS driver."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Callable


_PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DJI Transport Edge</title><style>:root{color-scheme:dark}body{margin:0;background:#0b1018;color:#e9edf5;font:14px system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:32px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #253044;padding-bottom:20px}h1{font-size:24px;margin:0}.sub,.muted{color:#91a0b7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:22px 0}.card{background:#121a27;border:1px solid #253044;border-radius:12px;padding:16px}.label{font-size:12px;color:#91a0b7;text-transform:uppercase;letter-spacing:.07em}.value{font-size:20px;margin-top:6px;overflow-wrap:anywhere}.good{color:#68d391}.warn{color:#f6c566}button{border:0;border-radius:8px;padding:9px 14px;background:#d75757;color:#fff;cursor:pointer}details{background:#121a27;border:1px solid #253044;border-radius:12px;padding:14px;margin-top:14px}summary{cursor:pointer}.streams{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}.stream{background:#121a27;border:1px solid #253044;border-radius:12px;padding:16px}dl{display:grid;grid-template-columns:max-content 1fr;gap:7px 12px;margin:12px 0 0}dt{color:#91a0b7}dd{margin:0;font-variant-numeric:tabular-nums}</style></head><body><main><header><div><h1>DJI Transport Edge</h1><div class='sub'>Direct Android → ROS 2 transport measurements</div></div><button id='exit'>Exit</button></header><section class='grid' id='summary'><div class='card'>Loading…</div></section><section class='streams' id='streams'></section><details><summary>Configuration and raw diagnostics</summary><pre id='raw'></pre></details></main><script>const q=s=>document.querySelector(s),n=v=>v??'—',fps=v=>v==null?'—':Number(v).toFixed(1)+' FPS',bit=v=>v==null?'—':(Number(v)/1e6).toFixed(2)+' Mbps';function card(label,value,klass=''){return `<div class="card"><div class="label">${label}</div><div class="value ${klass}">${value}</div></div>`}function stream(v){let r=v.rtp||{},res=v.resolution||{},c=v.context||{};return `<article class="stream"><strong>${v.name.toUpperCase()}</strong><dl><dt>Resolution</dt><dd>${res.width||0}×${res.height||0}</dd><dt>RTP input</dt><dd>${fps(r.estimated_fps)} · ${bit(r.estimated_bitrate_bps)}</dd><dt>Decoded / ROS</dt><dd>${n(v.decoded_frames)} / ${n(v.published_frames)}</dd><dt>Dropped old</dt><dd>${n(v.dropped_old_frames)}</dd><dt>Frame context</dt><dd>${n(c.published)} published / ${n(c.unavailable)} unavailable</dd><dt>RTP gaps</dt><dd>${n(r.sequence_gaps)}</dd><dt>Raw RTP capture</dt><dd>${v.capture_rtp?'on':'off'}</dd><dt>Pipeline</dt><dd>${v.error||'running'}</dd></dl></article>`}async function refresh(){try{let s=await (await fetch('/v1/state')).json(),c=s.clock||{},t=s.transport||{},cfg=s.configuration||{};q('#summary').innerHTML=card('Driver state',s.status,s.status==='ok'?'good':'warn')+card('Clock',c.ready?'ready':'not configured',c.ready?'good':'warn')+card('Accepted / rejected',`${n(t.accepted_packets)} / ${n(t.rejected_packets)}`)+card('Evidence session',`<span title="${n(s.evidence?.path)}">${n(s.evidence?.path)}</span>`);q('#streams').innerHTML=(s.video||[]).map(stream).join('');q('#raw').textContent=JSON.stringify({configuration:cfg,android_pre_network:s.android_pre_network,edge_post_network:s.edge_post_network,navigation:s.navigation,udp_errors:s.udp_errors},null,2)}catch(e){q('#summary').innerHTML=card('Dashboard connection','lost','warn')}}q('#exit').onclick=async()=>{await fetch('/v1/exit',{method:'POST'});q('#summary').innerHTML=card('Driver state','stopping','warn')};refresh();setInterval(refresh,1000);</script></body></html>"""


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
