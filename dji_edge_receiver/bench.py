"""Shared bounded-window transport measurement helpers.

Both the terminal script and the dashboard use this module so a report means
the same thing regardless of how it was started.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable


def _video_by_name(sample: dict) -> dict[str, dict]:
    video = sample.get("receiver_health", {}).get("video", [])
    return {
        item["name"]: item
        for item in video
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def _delta(last: dict, first: dict, keys: tuple[str, ...]) -> dict:
    return {
        key: int(last.get(key, 0) or 0) - int(first.get(key, 0) or 0)
        for key in keys
    }


def _latest_health_data(sample: dict) -> dict:
    health = sample.get("health") or {}
    data = health.get("data", {}) if isinstance(health, dict) else {}
    return data if isinstance(data, dict) else {}


def summary(samples: list[dict]) -> dict:
    """Return deltas for exactly the sampled window."""
    if not samples:
        raise ValueError("at least one state sample is required")
    last = samples[-1]
    first = samples[0]
    transport = last.get("transport", {})
    starting = first.get("transport", {})
    health_data = _latest_health_data(last)
    starting_health_data = _latest_health_data(first)
    frames = last.get("video_frames", {})
    sources = last.get("sources", {})
    video = last.get("receiver_health", {}).get("video", [])
    video_metrics = {
        item.get("name"): item.get("stats", {})
        for item in video
        if isinstance(item, dict) and item.get("name")
    }
    first_video = _video_by_name(first)
    last_video = _video_by_name(last)
    duration_ns = max(1, int(last.get("edge_mono_ns", 0)) - int(first.get("edge_mono_ns", 0)))
    duration_s = duration_ns / 1_000_000_000
    video_delta = {}
    for name, item in last_video.items():
        end_stats = item.get("stats", {})
        start_stats = first_video.get(name, {}).get("stats", {})
        counters = _delta(end_stats, start_stats, (
            "packets_received", "bytes_received", "packets_rejected", "sequence_gaps",
            "duplicates", "out_of_order", "access_units_observed", "kernel_socket_drops",
        ))
        expected = counters["packets_received"] + counters["sequence_gaps"]
        counters["observed_access_unit_fps"] = round(counters["access_units_observed"] / duration_s, 3)
        counters["observed_bitrate_bps"] = round(counters["bytes_received"] * 8 / duration_s)
        counters["sequence_gap_ratio"] = None if expected <= 0 else round(
            counters["sequence_gaps"] / expected, 6
        )
        video_delta[name] = counters

    packet_stats_delta = {}
    for packet_type, values in last.get("packet_stats", {}).items():
        packet_stats_delta[packet_type] = _delta(
            values, first.get("packet_stats", {}).get(packet_type, {}),
            ("datagrams", "bytes", "complete_samples"),
        )

    sender_delta = _delta(health_data, starting_health_data, (
        "primary_video_callbacks", "secondary_video_callbacks",
        "primary_access_units", "secondary_access_units",
        "primary_rtp_packets", "secondary_rtp_packets",
        "primary_video_callback_drops", "secondary_video_callback_drops",
        "telemetry_queue_drops", "telemetry_socket_errors", "video_socket_errors",
    ))
    return {
        "session": last.get("session"), "sample_count": len(samples), "clock": last.get("clock"),
        "frames_present": sorted(frames), "video_metrics": video_metrics,
        "video_delta": video_delta, "telemetry_sources_present": sorted(sources),
        "packet_stats": last.get("packet_stats", {}), "packet_stats_delta": packet_stats_delta,
        "callback_hz": health_data.get("callback_hz", {}),
        "sender_health": {key: health_data.get(key) for key in (
            "telemetry_queue_drops", "telemetry_socket_errors", "primary_video_callback_drops",
            "secondary_video_callback_drops", "video_socket_errors",
        )},
        "sender_delta": sender_delta, "measurement_duration_s": round(duration_s, 3),
        "receiver_delta": {key: int(transport.get(key, 0)) - int(starting.get(key, 0)) for key in (
            "accepted_packets", "rejected_packets", "sequence_gaps", "rtp_packets", "rtp_bytes",
            "rtp_rejected", "rtp_sequence_gaps", "timestamp_regressions",
        )},
        "last_frame_associations": {
            stream: value.get("telemetry_associations", {}) for stream, value in frames.items()
        },
    }


def build_report(samples: list[dict], *, state_url: str, requested_duration_s: float) -> dict:
    return {
        "schema_version": 1,
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "state_url": state_url,
        "duration_s": requested_duration_s,
        "summary": summary(samples),
        "samples": samples,
    }


def write_report(path: Path, report: dict) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing evidence: {path}")
    if not path.parent.is_dir():
        raise FileNotFoundError(f"output parent does not exist: {path.parent}")
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
