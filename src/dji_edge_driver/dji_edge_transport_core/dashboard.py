"""Dependency-free local measurement dashboard for the direct ROS driver."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Callable


_PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DJI Transport Edge</title><style>:root{color-scheme:dark}body{margin:0;background:#0b1018;color:#e9edf5;font:14px system-ui,sans-serif}main{max-width:1100px;margin:0 auto;padding:32px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #253044;padding-bottom:20px}h1{font-size:24px;margin:0}.sub,.muted{color:#91a0b7}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:22px 0}.card{background:#121a27;border:1px solid #253044;border-radius:12px;padding:16px}.label{font-size:12px;color:#91a0b7;text-transform:uppercase;letter-spacing:.07em}.value{font-size:20px;margin-top:6px;overflow-wrap:anywhere}.good{color:#68d391}.warn{color:#f6c566}button{border:0;border-radius:8px;padding:9px 14px;background:#d75757;color:#fff;cursor:pointer}details{background:#121a27;border:1px solid #253044;border-radius:12px;padding:14px;margin-top:14px}summary{cursor:pointer}.streams{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}.stream{background:#121a27;border:1px solid #253044;border-radius:12px;padding:16px}dl{display:grid;grid-template-columns:max-content 1fr;gap:7px 12px;margin:12px 0 0}dt{color:#91a0b7}dd{margin:0;font-variant-numeric:tabular-nums}</style></head><body><main><header><div><h1>DJI Transport Edge</h1><div class='sub'>Direct Android → ROS 2 transport measurements</div></div><button id='exit'>Exit</button></header><section class='grid' id='summary'><div class='card'>Loading…</div></section><section class='streams' id='streams'></section><details><summary>Configuration and raw diagnostics</summary><pre id='raw'></pre></details></main><script>const q=s=>document.querySelector(s),n=v=>v??'—',fps=v=>v==null?'—':Number(v).toFixed(1)+' FPS',bit=v=>v==null?'—':(Number(v)/1e6).toFixed(2)+' Mbps';function card(label,value,klass=''){return `<div class="card"><div class="label">${label}</div><div class="value ${klass}">${value}</div></div>`}function stream(v){let r=v.rtp||{},res=v.resolution||{},c=v.context||{};return `<article class="stream"><strong>${v.name.toUpperCase()}</strong><dl><dt>Resolution</dt><dd>${res.width||0}×${res.height||0}</dd><dt>RTP input</dt><dd>${fps(r.estimated_fps)} · ${bit(r.estimated_bitrate_bps)}</dd><dt>Decoded / ROS</dt><dd>${n(v.decoded_frames)} / ${n(v.published_frames)}</dd><dt>Dropped old</dt><dd>${n(v.dropped_old_frames)}</dd><dt>Frame context</dt><dd>${n(c.published)} published / ${n(c.unavailable)} unavailable</dd><dt>RTP gaps</dt><dd>${n(r.sequence_gaps)}</dd><dt>Raw RTP capture</dt><dd>${v.capture_rtp?'on':'off'}</dd><dt>Pipeline</dt><dd>${v.error||'running'}</dd></dl></article>`}async function refresh(){try{let s=await (await fetch('/v1/state')).json(),c=s.clock||{},t=s.transport||{},cfg=s.configuration||{};q('#summary').innerHTML=card('Driver state',s.status,s.status==='ok'?'good':'warn')+card('Clock',c.ready?'ready':'not configured',c.ready?'good':'warn')+card('Accepted / rejected',`${n(t.accepted_packets)} / ${n(t.rejected_packets)}`)+card('Evidence session',`<span title="${n(s.evidence?.path)}">${n(s.evidence?.path)}</span>`);q('#streams').innerHTML=(s.video||[]).map(stream).join('');q('#raw').textContent=JSON.stringify({configuration:cfg,android_pre_network:s.android_pre_network,edge_post_network:s.edge_post_network,navigation:s.navigation,udp_errors:s.udp_errors},null,2)}catch(e){q('#summary').innerHTML=card('Dashboard connection','lost','warn')}}q('#exit').onclick=async()=>{await fetch('/v1/exit',{method:'POST'});q('#summary').innerHTML=card('Driver state','stopping','warn')};refresh();setInterval(refresh,1000);</script></body></html>"""

_PAGE += """<script>document.addEventListener('DOMContentLoaded',async()=>{let d=document.querySelector('details');d.insertAdjacentHTML('beforebegin',`<section id="transportConfig" class="card"><div class="label">Local runtime configuration</div><p class="muted">Only these values are saved locally. Ports and RTP are fixed. Ubuntu address: <span id="ubuntuIp">—</span></p><label>Tablet clock IP / hostname <input id="clockHost"></label><label><input id="capture" type="checkbox"> Capture raw RTP</label><label><input id="preview" type="checkbox"> Native preview windows</label><p><button id="save" style="background:#356ee8">Save</button> <button id="saveRestart" style="background:#d08b32">Save and restart</button> <span id="configStatus" class="muted"></span></p></section>`);let c=await (await fetch('/v1/config')).json();ubuntuIp.textContent=(c.ubuntu_ipv4||[]).join(', ')||'unavailable';clockHost.value=c.values.android_clock_host;capture.checked=c.values.capture_rtp;preview.checked=c.values.preview_windows;async function save(restart){let r=await fetch('/v1/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({values:{android_clock_host:clockHost.value,capture_rtp:capture.checked,preview_windows:preview.checked},restart})}),j=await r.json();configStatus.textContent=j.error||j.status||'saved'}save.onclick=()=>save(false);saveRestart.onclick=()=>save(true)})</script>"""

_PAGE += """<script>document.addEventListener('DOMContentLoaded',()=>{let main=document.querySelector('main'),header=main.querySelector('header'),transport=document.createElement('section'),map=document.createElement('section');transport.id='transportPanel';map.id='mapPanel';map.hidden=true;header.insertAdjacentHTML('afterend',`<style>.tabs{display:flex;gap:8px;margin:18px 0}.tab{background:#253044;color:#e9edf5}.tab.active{background:#356ee8}label{display:inline-flex;gap:8px;align-items:center;margin:6px 14px 6px 0}input{background:#0b1018;color:#e9edf5;border:1px solid #42516b;border-radius:6px;padding:6px;width:130px}</style><nav class="tabs"><button class="tab active" data-panel="transportPanel">Transport</button><button class="tab" data-panel="mapPanel">Map &amp; Path</button></nav>`);for(let id of ['summary','streams','transportConfig']){let e=document.getElementById(id);if(e)transport.append(e)}transport.append(document.querySelector('details'));main.append(transport,map);map.innerHTML=`<section class="card"><div class="label">Map &amp; path</div><p class="muted">Only path spacing and retention are editable. Map, calibration, UTM and safety gates remain protected.</p><div class="grid" id="mapperMetrics"><div class="card">Loading mapper status…</div></div><label>Path spacing (m) <input id="pathSpacing" type="number" min="0" step="0.01"></label><label><input id="keepFullRoute" type="checkbox"> Keep full route</label><label id="historyRow">Maximum path points <input id="maxHistory" type="number" min="0" step="1"></label><p class="muted">Full route keeps every accepted pose and grows memory with mission duration.</p><p><button id="saveMapper" style="background:#356ee8">Save</button> <button id="saveMapperRestart" style="background:#d08b32">Save and restart</button> <span id="mapperConfigStatus" class="muted"></span></p></section>`;for(let tab of document.querySelectorAll('.tab'))tab.onclick=()=>{for(let button of document.querySelectorAll('.tab'))button.classList.toggle('active',button===tab);for(let panel of [transport,map])panel.hidden=panel.id!==tab.dataset.panel};let n=v=>v??'—',card=(label,value,klass='')=>`<div class="card"><div class="label">${label}</div><div class="value ${klass}">${value}</div></div>`,spacing=q('#pathSpacing'),keep=q('#keepFullRoute'),history=q('#maxHistory'),row=q('#historyRow'),status=q('#mapperConfigStatus');function updateHistory(){row.hidden=keep.checked}async function refreshMapper(){try{let m=await(await fetch('/v1/mapper-config')).json(),s=m.status||{},v=s.values||{};spacing.value=m.values.min_path_spacing_m;keep.checked=m.values.max_history_points===0;history.value=m.values.max_history_points;updateHistory();let state=s.state||'unavailable',klass=state==='ok'?'good':'warn';q('#mapperMetrics').innerHTML=card('Mapper state',state,klass)+card('Path poses',n(v.path_poses))+card('Accepted',n(v.accepted))+card('RTK / GPS fallback',`${n(v.accepted_rtk)} / ${n(v.accepted_gps_fallback)}`)+card('Rejected',n(v.rejected))+card('Frame contexts',`${n(v.frame_context_published)} / ${n(v.frame_context_unavailable)}`);if(s.error)status.textContent=s.error;else if(m.pending_restart)status.textContent='saved; restart required to apply';else status.textContent=state==='ok'?'active':'mapper status unavailable'}catch(e){q('#mapperMetrics').innerHTML=card('Mapper state','unavailable','warn')}}async function saveMapper(restart){let values={min_path_spacing_m:Number(spacing.value),max_history_points:keep.checked?0:Number(history.value)},r=await fetch('/v1/mapper-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({values,restart})}),j=await r.json();status.textContent=j.error||j.status||'saved'}keep.onchange=updateHistory;q('#saveMapper').onclick=()=>saveMapper(false);q('#saveMapperRestart').onclick=()=>saveMapper(true);refreshMapper();setInterval(refreshMapper,1000)})</script>"""


class DashboardServer:
    """Small loopback HTTP server owned by the driver process."""

    def __init__(self, host: str, port: int, state_provider: Callable[[], dict[str, Any]], stop_callback: Callable[[], None], config_provider: Callable[[], dict[str, Any]] | None = None, config_saver: Callable[[dict[str, Any], bool], dict[str, Any]] | None = None, mapper_config_provider: Callable[[], dict[str, Any]] | None = None, mapper_config_saver: Callable[[dict[str, Any], bool], dict[str, Any]] | None = None) -> None:
        self._state_provider = state_provider
        self._stop_callback = stop_callback
        self._config_provider = config_provider
        self._config_saver = config_saver
        self._mapper_config_provider = mapper_config_provider
        self._mapper_config_saver = mapper_config_saver

        parent = self
        class Handler(BaseHTTPRequestHandler):
            def _json(self, payload: dict[str, Any], status: int = 200) -> None:
                body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
                self.send_response(status); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self) -> None:
                if self.path == "/":
                    body = _PAGE.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
                if self.path in {"/v1/state", "/health"}:
                    state = parent._state_provider()
                    self._json(state if self.path == "/v1/state" else {"status": state.get("status", "unknown")})
                    return
                if self.path == "/v1/config" and parent._config_provider is not None:
                    self._json(parent._config_provider()); return
                if self.path == "/v1/mapper-config" and parent._mapper_config_provider is not None:
                    self._json(parent._mapper_config_provider()); return
                self.send_error(404)
            def do_POST(self) -> None:
                saver = parent._config_saver if self.path == "/v1/config" else parent._mapper_config_saver if self.path == "/v1/mapper-config" else None
                if saver is not None:
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                        if not isinstance(body, dict):
                            raise ValueError("configuration request must be a JSON object")
                        restart = body.get("restart")
                        if not isinstance(restart, bool):
                            raise ValueError("restart must be boolean")
                        payload = saver(body.get("values"), restart)
                    except (TypeError, ValueError, json.JSONDecodeError) as error:
                        self._json({"error": str(error)}, 400); return
                    except OSError as error:
                        self._json({"error": str(error)}, 500); return
                    self._json(payload, 202 if payload.get("restarting") else 200); return
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


def start_optional_dashboard(
    host: str,
    port: int,
    state_provider: Callable[[], dict[str, Any]],
    stop_callback: Callable[[], None],
    config_provider: Callable[[], dict[str, Any]] | None = None,
    config_saver: Callable[[dict[str, Any], bool], dict[str, Any]] | None = None,
    mapper_config_provider: Callable[[], dict[str, Any]] | None = None,
    mapper_config_saver: Callable[[dict[str, Any], bool], dict[str, Any]] | None = None,
    *,
    on_error: Callable[[OSError], None],
) -> DashboardServer | None:
    """Start the optional local observer without making transport depend on it."""
    try:
        dashboard = DashboardServer(host, port, state_provider, stop_callback, config_provider, config_saver, mapper_config_provider, mapper_config_saver)
        dashboard.start()
        return dashboard
    except OSError as error:
        on_error(error)
        return None
