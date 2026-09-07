"""Dependency-free local measurement dashboard for the direct ROS driver."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any, Callable


_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>DJI Transport Edge</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; background: #0b1018; color: #e9edf5; font: 14px system-ui, sans-serif; }
    main { max-width: 1100px; margin: 0 auto; padding: 32px; }
    header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #253044; padding-bottom: 20px; }
    h1 { font-size: 24px; margin: 0; }
    .sub, .muted { color: #91a0b7; }
    .tabs { display: flex; gap: 8px; margin: 18px 0; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; margin: 22px 0; }
    .streams { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }
    .ingress { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 10px; margin: 0 0 14px; }
    .ingress .card { padding: 12px 14px; }
    .ingress .value { font-size: 15px; }
    .card, .stream, details { background: #121a27; border: 1px solid #253044; border-radius: 12px; padding: 16px; }
    .label { font-size: 12px; color: #91a0b7; text-transform: uppercase; letter-spacing: .07em; }
    .value { font-size: 20px; margin-top: 6px; overflow-wrap: anywhere; }
    .good { color: #68d391; }
    .warn { color: #f6c566; }
    button { border: 0; border-radius: 8px; padding: 9px 14px; background: #d75757; color: #fff; cursor: pointer; }
    button.tab { background: #253044; }
    button.tab.active, button.primary { background: #356ee8; }
    button.restart { background: #d08b32; }
    details { margin-top: 14px; }
    summary { cursor: pointer; }
    dl { display: grid; grid-template-columns: max-content 1fr; gap: 7px 12px; margin: 12px 0 0; }
    dt { color: #91a0b7; }
    dd { margin: 0; font-variant-numeric: tabular-nums; }
    label { display: inline-flex; gap: 8px; align-items: center; margin: 6px 14px 6px 0; }
    input { background: #0b1018; color: #e9edf5; border: 1px solid #42516b; border-radius: 6px; padding: 6px; width: 130px; }
    input[type="checkbox"] { width: auto; }
    [hidden] { display: none !important; }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>DJI Transport Edge</h1>
        <div class="sub">Direct Android → ROS 2 transport measurements</div>
      </div>
      <button id="exit">Exit</button>
    </header>

    <nav class="tabs">
      <button class="tab active" data-panel="transportPanel">Transport</button>
      <button class="tab" data-panel="mapPanel">Map &amp; Path</button>
    </nav>

    <section id="transportPanel">
      <section class="grid" id="summary"><div class="card">Loading…</div></section>
      <section class="ingress" id="ingress" aria-label="Android ingress"></section>
      <section class="streams" id="streams"></section>
      <section class="card" id="transportConfig">
        <div class="label">Local runtime configuration</div>
        <p class="muted">Only these values are saved locally. Ports and RTP are fixed. Ubuntu address: <span id="ubuntuIp">—</span></p>
        <label>Tablet clock IP / hostname <input id="clockHost"></label>
        <label><input id="capture" type="checkbox"> Capture raw RTP</label>
        <label><input id="preview" type="checkbox"> Native preview windows</label>
        <p><button class="primary" id="save">Save</button> <button class="restart" id="saveRestart">Save and restart</button> <span class="muted" id="configStatus"></span></p>
      </section>
      <details>
        <summary>Configuration and raw diagnostics</summary>
        <pre id="raw"></pre>
      </details>
    </section>

    <section id="mapPanel" hidden>
      <section class="card">
        <div class="label">Map &amp; path</div>
        <p class="muted">Only path spacing and retention are editable. Map, calibration, UTM and safety gates remain protected.</p>
        <div class="grid" id="mapperMetrics"><div class="card">Loading mapper status…</div></div>
        <label>Path spacing (m) <input id="pathSpacing" type="number" min="0" step="0.01"></label>
        <label><input id="keepFullRoute" type="checkbox"> Keep full route</label>
        <label id="historyRow">Maximum path points <input id="maxHistory" type="number" min="0" step="1"></label>
        <p class="muted">Full route keeps every accepted pose and grows memory with mission duration.</p>
        <p><button class="primary" id="saveMapper">Save</button> <button class="restart" id="saveMapperRestart">Save and restart</button> <span class="muted" id="mapperConfigStatus"></span></p>
      </section>
    </section>
  </main>
  <script>
    const q = (selector) => document.querySelector(selector);
    const valueOrDash = (value) => value ?? "—";
    const formatFps = (value) => value == null ? "—" : `${Number(value).toFixed(1)} FPS`;
    const formatBitrate = (value) => value == null ? "—" : `${(Number(value) / 1e6).toFixed(2)} Mbps`;
    const formatAge = (value) => value == null ? "never" : value < 1 ? "just now" : `${Number(value).toFixed(1)} s ago`;
    const card = (label, value, style = "") => `<div class="card"><div class="label">${label}</div><div class="value ${style}">${value}</div></div>`;

    function streamCard(video) {
      const rtp = video.rtp || {};
      const resolution = video.resolution || {};
      const context = video.context || {};
      return `<article class="stream"><strong>${video.name.toUpperCase()}</strong><dl>
        <dt>Resolution</dt><dd>${resolution.width || 0}×${resolution.height || 0}</dd>
        <dt>RTP input</dt><dd>${formatFps(rtp.estimated_fps)} · ${formatBitrate(rtp.estimated_bitrate_bps)}</dd>
        <dt>Decoded / ROS</dt><dd>${valueOrDash(video.decoded_frames)} / ${valueOrDash(video.published_frames)}</dd>
        <dt>Dropped old</dt><dd>${valueOrDash(video.dropped_old_frames)}</dd>
        <dt>Frame context</dt><dd>${valueOrDash(context.published)} published / ${valueOrDash(context.unavailable)} unavailable</dd>
        <dt>RTP gaps</dt><dd>${valueOrDash(rtp.sequence_gaps)}</dd>
        <dt>Raw RTP capture</dt><dd>${video.capture_rtp ? "on" : "off"}</dd>
        <dt>Pipeline</dt><dd>${video.error || "running"}</dd>
      </dl></article>`;
    }

    function ingressCard(label, boundary) {
      if (!boundary) return "";
      const received = Number(boundary.datagrams_received || 0);
      const valid = Number(boundary.packets_valid || 0);
      const rejected = Number(boundary.packets_rejected || 0);
      const waiting = received === 0;
      const status = waiting ? "waiting for Android" : `${valid} valid · ${rejected} rejected`;
      const style = waiting || rejected ? "warn" : "good";
      return `<article class="card"><div class="label">${label}</div><div class="value ${style}">${status}</div><div class="muted">last datagram: ${formatAge(boundary.last_datagram_age_s)}</div></article>`;
    }

    async function refreshTransport() {
      try {
        const state = await (await fetch("/v1/state")).json();
        const clock = state.clock || {};
        const transport = state.transport || {};
        q("#summary").innerHTML =
          card("Driver state", state.status, state.status === "ok" ? "good" : "warn") +
          card("Clock", clock.ready ? "ready" : state.configuration?.android_clock_host ? "waiting for tablet" : "not configured", clock.ready ? "good" : "warn") +
          card("Accepted / rejected", `${valueOrDash(transport.accepted_packets)} / ${valueOrDash(transport.rejected_packets)}`) +
          card("Evidence session", `<span title="${valueOrDash(state.evidence?.path)}">${valueOrDash(state.evidence?.path)}</span>`);
        q("#streams").innerHTML = (state.video || []).map(streamCard).join("");
        const ingress = state.ingress || {};
        q("#ingress").innerHTML = [
          ingressCard("Telemetry", ingress.telemetry),
          ingressCard("Frame metadata", ingress.frame_metadata),
          ingressCard("Primary video", ingress.primary),
          ingressCard("FPV video", ingress.fpv),
        ].join("");
        q("#raw").textContent = JSON.stringify({
          configuration: state.configuration,
          android_pre_network: state.android_pre_network,
          edge_post_network: state.edge_post_network,
          ingress: state.ingress,
          navigation: state.navigation,
          udp_errors: state.udp_errors,
        }, null, 2);
      } catch (_) {
        q("#summary").innerHTML = card("Dashboard connection", "lost", "warn");
      }
    }

    function updateHistoryVisibility() {
      q("#historyRow").hidden = q("#keepFullRoute").checked;
    }

    let mapperDraftDirty = false;
    let mapperDraftInitialized = false;

    function mapperValuesText(values) {
      if (!values) return "unavailable";
      const history = values.max_history_points === 0 ? "full route" : `${values.max_history_points} points`;
      return `${values.min_path_spacing_m} m / ${history}`;
    }

    function hydrateMapperControls(values, force = false) {
      if (!values || (mapperDraftInitialized && mapperDraftDirty && !force)) return;
      q("#pathSpacing").value = values.min_path_spacing_m;
      q("#keepFullRoute").checked = values.max_history_points === 0;
      q("#maxHistory").value = values.max_history_points;
      mapperDraftDirty = false;
      mapperDraftInitialized = true;
      updateHistoryVisibility();
    }

    function mapperMetrics(payload) {
      const status = payload.status || {};
      const values = status.values || {};
      const state = status.state || "unavailable";
      const style = state === "ok" ? "good" : "warn";
      const contextText = `${valueOrDash(values.frame_context_received)} received / ${valueOrDash(values.frame_context_published)} published / ${valueOrDash(values.frame_context_rejected)} rejected / ${valueOrDash(values.frame_context_unavailable)} unavailable`;
      return card("Mapper status", state, style) +
        card("Active path", mapperValuesText(payload.active_values)) +
        card("Pending override", payload.pending_restart ? mapperValuesText(payload.requested_values) : "none") +
        card("Path poses", valueOrDash(values.path_poses)) +
        card("Accepted", valueOrDash(values.accepted)) +
        card("RTK / GPS fallback", `${valueOrDash(values.accepted_rtk)} / ${valueOrDash(values.accepted_gps_fallback)}`) +
        card("Rejected", valueOrDash(values.rejected)) +
        card("Frame contexts", contextText);
    }

    async function refreshMapper(forceHydrate = false) {
      try {
        const mapper = await (await fetch("/v1/mapper-config")).json();
        hydrateMapperControls(mapper.values, forceHydrate);
        q("#mapperMetrics").innerHTML = mapperMetrics(mapper);
        const status = mapper.status || {};
        if (status.error) q("#mapperConfigStatus").textContent = status.error;
        else if (mapper.pending_restart) q("#mapperConfigStatus").textContent = "saved; waiting for mapper restart to apply";
        else if ((status.state || "unavailable") !== "ok") q("#mapperConfigStatus").textContent = `mapper status ${(status.state || "unavailable")}`;
        else q("#mapperConfigStatus").textContent = "mapper status live";
      } catch (_) {
        q("#mapperMetrics").innerHTML = card("Mapper status", "unavailable", "warn");
      }
    }

    async function saveMapper(restart) {
      const values = {
        min_path_spacing_m: Number(q("#pathSpacing").value),
        max_history_points: q("#keepFullRoute").checked ? 0 : Number(q("#maxHistory").value),
      };
      const response = await fetch("/v1/mapper-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values, restart }),
      });
      const result = await response.json();
      q("#mapperConfigStatus").textContent = result.error || result.status || "saved";
      if (response.ok) {
        mapperDraftDirty = false;
        await refreshMapper(true);
      }
    }

    async function loadTransportConfig() {
      const config = await (await fetch("/v1/config")).json();
      q("#ubuntuIp").textContent = (config.ubuntu_ipv4 || []).join(", ") || "unavailable";
      q("#clockHost").value = config.values.android_clock_host;
      q("#capture").checked = config.values.capture_rtp;
      q("#preview").checked = config.values.preview_windows;
    }

    async function saveTransport(restart) {
      const response = await fetch("/v1/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          values: {
            android_clock_host: q("#clockHost").value,
            capture_rtp: q("#capture").checked,
            preview_windows: q("#preview").checked,
          },
          restart,
        }),
      });
      const result = await response.json();
      q("#configStatus").textContent = result.error || result.status || "saved";
    }

    for (const tab of document.querySelectorAll(".tab")) {
      tab.onclick = () => {
        for (const button of document.querySelectorAll(".tab")) button.classList.toggle("active", button === tab);
        q("#transportPanel").hidden = tab.dataset.panel !== "transportPanel";
        q("#mapPanel").hidden = tab.dataset.panel !== "mapPanel";
      };
    }
    for (const input of [q("#pathSpacing"), q("#maxHistory")]) input.oninput = () => { mapperDraftDirty = true; };
    q("#keepFullRoute").onchange = () => { mapperDraftDirty = true; updateHistoryVisibility(); };
    q("#saveMapper").onclick = () => saveMapper(false);
    q("#saveMapperRestart").onclick = () => saveMapper(true);
    q("#save").onclick = () => saveTransport(false);
    q("#saveRestart").onclick = () => saveTransport(true);
    q("#exit").onclick = async () => {
      await fetch("/v1/exit", { method: "POST" });
      q("#summary").innerHTML = card("Driver state", "stopping", "warn");
    };

    loadTransportConfig().catch(() => { q("#configStatus").textContent = "configuration unavailable"; });
    refreshTransport();
    refreshMapper();
    setInterval(refreshTransport, 1000);
    setInterval(refreshMapper, 1000);
  </script>
</body>
</html>"""


class DashboardServer:
    """Small loopback HTTP server owned by the driver process."""

    def __init__(
        self,
        host: str,
        port: int,
        state_provider: Callable[[], dict[str, Any]],
        stop_callback: Callable[[], None],
        config_provider: Callable[[], dict[str, Any]] | None = None,
        config_saver: Callable[[dict[str, Any], bool], dict[str, Any]] | None = None,
        mapper_config_provider: Callable[[], dict[str, Any]] | None = None,
        mapper_config_saver: Callable[[dict[str, Any], bool], dict[str, Any]] | None = None,
    ) -> None:
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
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == "/":
                    body = _PAGE.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path in {"/v1/state", "/health"}:
                    state = parent._state_provider()
                    self._json(state if self.path == "/v1/state" else {"status": state.get("status", "unknown")})
                    return
                if self.path == "/v1/config" and parent._config_provider is not None:
                    self._json(parent._config_provider())
                    return
                if self.path == "/v1/mapper-config" and parent._mapper_config_provider is not None:
                    self._json(parent._mapper_config_provider())
                    return
                self.send_error(404)

            def do_POST(self) -> None:
                saver = (
                    parent._config_saver if self.path == "/v1/config"
                    else parent._mapper_config_saver if self.path == "/v1/mapper-config"
                    else None
                )
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
                        self._json({"error": str(error)}, 400)
                        return
                    except OSError as error:
                        self._json({"error": str(error)}, 500)
                        return
                    self._json(payload, 202 if payload.get("restarting") else 200)
                    return
                if self.path != "/v1/exit":
                    self.send_error(404)
                    return
                parent._stop_callback()
                self._json({"stopping": True})

            def log_message(self, *_: object) -> None:
                pass

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
        dashboard = DashboardServer(
            host,
            port,
            state_provider,
            stop_callback,
            config_provider,
            config_saver,
            mapper_config_provider,
            mapper_config_saver,
        )
        dashboard.start()
        return dashboard
    except OSError as error:
        on_error(error)
        return None
