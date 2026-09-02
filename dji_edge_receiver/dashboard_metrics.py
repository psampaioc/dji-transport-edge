"""Pure dashboard view-model helpers; they never touch receiver ingest."""

from __future__ import annotations

import socket
import time
from typing import Callable

from .config import ReceiverConfig


FRESHNESS_NS = 3_000_000_000


def local_ipv4_addresses(
    getaddrinfo: Callable = socket.getaddrinfo,
    route_probe: Callable[[], str | None] | None = None,
) -> list[str]:
    """Best-effort non-loopback addresses suitable for the tablet UI."""
    values: set[str] = set()
    if route_probe is None:
        def route_probe() -> str | None:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # UDP connect selects the active route without transmitting a packet.
                probe.connect(("192.0.2.1", 9))
                return str(probe.getsockname()[0])
            except OSError:
                return None
            finally:
                probe.close()
    try:
        entries = getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        entries = []
    for _, _, _, _, address in entries:
        ip = address[0]
        if not ip.startswith("127."):
            values.add(ip)
    route_ip = route_probe()
    if route_ip and not route_ip.startswith("127."):
        values.add(route_ip)
    return sorted(values)


def _age_ms(now_ns: int, timestamp_ns: int | None) -> float | None:
    if timestamp_ns is None:
        return None
    return round(max(0, now_ns - timestamp_ns) / 1_000_000, 1)


def _feed_model(item: dict, evidence: dict, now_ns: int) -> dict:
    stats = item.get("stats", {}) if isinstance(item.get("stats"), dict) else {}
    name = str(item.get("name", "unknown"))
    pipeline = item.get("pipeline_status", "unknown")
    last_packet = item.get("last_packet_mono_ns")
    age_ms = _age_ms(now_ns, last_packet if isinstance(last_packet, int) else None)
    packets = int(stats.get("packets_received", 0) or 0)
    relay_errors = int(item.get("relay_errors", 0) or 0)
    kernel_drops = int(stats.get("kernel_socket_drops", 0) or 0)
    gaps = int(stats.get("sequence_gaps", 0) or 0)
    if pipeline == "disabled":
        status, explanation = "disabled", f"{name.title()} pipeline is disabled in the active configuration."
    elif pipeline == "exited":
        status, explanation = "exited", f"{name.title()} decoder pipeline exited; inspect its native window or GStreamer output."
    elif packets == 0:
        status, explanation = "waiting", f"{name.title()} has not received RTP at its configured UDP port."
    elif age_ms is not None and age_ms > FRESHNESS_NS / 1_000_000:
        status, explanation = "degraded", f"{name.title()} last RTP packet is {age_ms:.0f} ms old."
    elif relay_errors or kernel_drops or gaps or evidence.get("write_errors", 0):
        reasons = []
        if gaps:
            reasons.append(f"{gaps} RTP sequence gaps")
        if kernel_drops:
            reasons.append(f"{kernel_drops} kernel UDP drops")
        if relay_errors:
            reasons.append(f"{relay_errors} relay errors")
        if evidence.get("write_errors", 0):
            reasons.append("evidence writer errors")
        status, explanation = "degraded", f"{name.title()} is receiving RTP with " + ", ".join(reasons) + "."
    else:
        status, explanation = "receiving", f"{name.title()} RTP is current; native GStreamer owns live decoding."
    h264 = stats.get("h264", {}) if isinstance(stats.get("h264"), dict) else {}
    return {
        "name": name, "status": status, "explanation": explanation,
        "pipeline_status": pipeline, "last_packet_age_ms": age_ms,
        "resolution": h264.get("stream_info"), "idr_age_ms": h264.get("last_idr_age_ms"),
        "estimated_fps": stats.get("estimated_fps"), "estimated_bitrate_bps": stats.get("estimated_bitrate_bps"),
        "decoded_frame_count_available": bool(stats.get("decoded_frame_count_available", False)),
        "measurement_boundary": stats.get("measurement_boundary"),
        "counters": {
            key: stats.get(key, 0) for key in (
                "packets_received", "bytes_received", "packets_rejected", "sequence_gaps",
                "duplicates", "out_of_order", "access_units_observed", "kernel_socket_drops",
            )
        } | {"relay_errors": relay_errors},
    }


def _delay(record: dict | None, clock: dict) -> dict:
    if not clock.get("ready"):
        return {"available": False, "reason": "Clock mapping is not ready."}
    if not isinstance(record, dict):
        return {"available": False, "reason": "No timestamped source sample is available."}
    mapped = record.get("mapped_edge_mono_ns")
    received = record.get("edge_receive_mono_ns")
    if not isinstance(mapped, int) or not isinstance(received, int):
        return {"available": False, "reason": "This source has no mapped Android timestamp."}
    return {
        "available": True,
        "callback_to_edge_ms": round(max(0, received - mapped) / 1_000_000, 3),
        "boundary": "Android callback timestamp to Ubuntu UDP receive timestamp; not camera exposure-to-screen latency.",
    }


def dashboard_snapshot(state: dict, health: dict, config: ReceiverConfig, *, now_ns: int | None = None) -> dict:
    now = time.monotonic_ns() if now_ns is None else now_ns
    evidence = health.get("evidence", {}) if isinstance(health.get("evidence"), dict) else {}
    feeds = [_feed_model(item, evidence, now) for item in health.get("video", []) if isinstance(item, dict)]
    frames = state.get("video_frames", {}) if isinstance(state.get("video_frames"), dict) else {}
    sources = state.get("sources", {}) if isinstance(state.get("sources"), dict) else {}
    timing = {
        "clock": state.get("clock", {}),
        "video": {name: _delay(record, state.get("clock", {})) for name, record in frames.items()},
        "telemetry": {name: _delay(record, state.get("clock", {})) for name, record in sources.items()},
    }
    active = {stream.name: stream for stream in config.video_streams}
    return {
        "generated_edge_mono_ns": now,
        "receiver_status": health.get("status", "unknown"),
        "feeds": feeds,
        "network": {
            "tablet_clock_ip": config.network.android_clock_host,
            "receiver_bind_host": config.network.bind_host,
            "dashboard_url": f"http://{config.dashboard.host}:{config.dashboard.port}",
            "local_ipv4_addresses": local_ipv4_addresses(),
        },
        "configuration": {
            "storage_evidence_dir": str(config.storage.evidence_dir),
            "capture_rtp": config.storage.capture_rtp,
            "feeds": {name: {"input_port": stream.input_port, "pipeline_port": stream.pipeline_port,
                               "gstreamer_enabled": stream.gstreamer_enabled}
                      for name, stream in active.items()},
        },
        "telemetry": {
            "flight": state.get("flight"), "rtk": state.get("rtk"), "gimbals": state.get("gimbals", []),
            "android_health": state.get("health"), "sources": sources,
        },
        "timing": timing,
        "impact": {"evidence": evidence, "receiver_transport": state.get("transport", {})},
    }
