#!/usr/bin/env python3
"""Generate or verify deterministic transport-v1 protocol golden data."""

from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "deploy/protocol/v1/golden_packets.json"
MAX_DATAGRAM_BYTES = 1200


def rtp_packet(seq: int, timestamp: int, ssrc: int, marker: bool, payload: bytes) -> bytes:
    return struct.pack("!BBHII", 0x80, (0x80 if marker else 0) | 96, seq, timestamp, ssrc) + payload


def expected_fixture() -> dict[str, Any]:
    session = "fixture-session-0001"
    packets = [
        {
            "v": 1,
            "type": "flight",
            "session": session,
            "seq": 42,
            "rx_mono_ns": 1234567890123,
            "rx_utc_ns": 1787654321000000000,
            "source_ts_ns": None,
            "lat": 38.7223,
            "lon": -9.1393,
            "alt_m": 12.5,
            "vel_mps": [1.25, -0.5, 0.0],
            "heading_deg": 359.5,
            "flight_mode": "GPS_ATTI",
            "gps_signal": 5,
            "satellites": 17,
            "is_flying": False,
            "motors_on": False,
            "valid": True,
        },
        {
            "v": 1,
            "type": "rtk",
            "session": session,
            "seq": 11,
            "rx_mono_ns": 1234567890220,
            "rx_utc_ns": 1787654321000001097,
            "source_ts_ns": None,
            "lat": 38.7223001,
            "lon": -9.1392999,
            "alt_m": 13.1,
            "solution": "FIXED_POINT",
            "heading_solution": "FIXED_POINT",
            "is_rtk_used": True,
            "valid": True,
        },
        {
            "v": 1,
            "type": "gimbal",
            "session": session,
            "seq": 77,
            "rx_mono_ns": 1234567890330,
            "rx_utc_ns": 1787654321000001207,
            "source_ts_ns": None,
            "index": 0,
            "pitch_deg": -45.25,
            "roll_deg": 0.1,
            "yaw_deg": 179.9,
            "mode": "YAW_FOLLOW",
            "connected": True,
            "valid": True,
        },
        {
            "v": 1,
            "type": "health",
            "session": session,
            "seq": 3,
            "rx_mono_ns": 1234567890440,
            "rx_utc_ns": 1787654321000001317,
            "sender_drops": 0,
            "serialization_errors": 0,
            "socket_errors": 0,
        },
        {
            "v": 1,
            "type": "video_au",
            "session": session,
            "feed": "primary",
            "source": "FPV_CAM",
            "frame_seq": 9321,
            "rtp_ssrc": 305419896,
            "rtp_ts": 987654321,
            "au_first_byte_rx_mono_ns": 1234567890500,
            "au_complete_rx_mono_ns": 1234567891987,
            "idr": True,
            "bytes": 24873,
        },
    ]

    ssrc = 0x12345678
    timestamp = 987654321
    sps = rtp_packet(1000, timestamp, ssrc, False, bytes.fromhex("6742c01fda0280b7fe0501"))
    idr = bytes.fromhex("65") + bytes(range(1, 33))
    fu_indicator = bytes([(idr[0] & 0xE0) | 28])
    fu_start = rtp_packet(1001, timestamp, ssrc, False, fu_indicator + bytes([0x80 | 5]) + idr[1:17])
    fu_end = rtp_packet(1002, timestamp, ssrc, True, fu_indicator + bytes([0x40 | 5]) + idr[17:])
    rtp = [
        {"name": "sps-single-nal", "base64": base64.b64encode(sps).decode()},
        {"name": "idr-fua-start", "base64": base64.b64encode(fu_start).decode()},
        {"name": "idr-fua-end", "base64": base64.b64encode(fu_end).decode()},
    ]
    return {
        "protocol": "matrice-transport-v1",
        "max_datagram_bytes": MAX_DATAGRAM_BYTES,
        "telemetry_json": packets,
        "rtp_h264": rtp,
    }


def compact_json(packet: dict[str, Any]) -> bytes:
    return json.dumps(packet, separators=(",", ":"), sort_keys=True).encode("utf-8")


def validate(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data != expected_fixture():
        errors.append("fixture content differs from deterministic v1 golden data")
    packets = data.get("telemetry_json", [])
    for index, packet in enumerate(packets):
        encoded = compact_json(packet)
        if len(encoded) > MAX_DATAGRAM_BYTES:
            errors.append(f"telemetry_json[{index}] is {len(encoded)} bytes")
        for field in ("v", "type", "session"):
            if field not in packet:
                errors.append(f"telemetry_json[{index}] lacks {field}")
        if packet.get("type") != "video_au" and "rx_mono_ns" not in packet:
            errors.append(f"telemetry_json[{index}] lacks rx_mono_ns")

    previous_seq: int | None = None
    for index, item in enumerate(data.get("rtp_h264", [])):
        try:
            raw = base64.b64decode(item["base64"], validate=True)
            first, second, seq, timestamp, ssrc = struct.unpack("!BBHII", raw[:12])
        except (KeyError, ValueError, struct.error) as exc:
            errors.append(f"rtp_h264[{index}] cannot be decoded: {exc}")
            continue
        if first != 0x80 or (second & 0x7F) != 96:
            errors.append(f"rtp_h264[{index}] has an invalid RTP v2/PT96 header")
        if timestamp != 987654321 or ssrc != 0x12345678:
            errors.append(f"rtp_h264[{index}] clock/SSRC differs")
        if previous_seq is not None and seq != previous_seq + 1:
            errors.append(f"rtp_h264[{index}] sequence is not contiguous")
        previous_seq = seq
    return errors


def validate_with_edge_receiver(data: dict[str, Any]) -> tuple[list[str], bool]:
    edge_root = ROOT
    if not (edge_root / "dji_edge_receiver/protocol.py").exists():
        return [], False
    sys.path.insert(0, str(edge_root))
    try:
        from dji_edge_receiver.protocol import (  # type: ignore[import-not-found]
            ProtocolError,
            decode_json_packet,
            parse_rtp_packet,
        )
    except ImportError as exc:
        return [f"edge receiver protocol module cannot be imported: {exc}"], True

    errors: list[str] = []
    for index, packet in enumerate(data.get("telemetry_json", [])):
        try:
            decode_json_packet(compact_json(packet), expected_version=1, max_bytes=MAX_DATAGRAM_BYTES)
        except ProtocolError as exc:
            errors.append(f"edge receiver rejected telemetry_json[{index}]: {exc}")
    for index, item in enumerate(data.get("rtp_h264", [])):
        try:
            raw = base64.b64decode(item["base64"], validate=True)
            parse_rtp_packet(raw, expected_payload_type=96)
        except (KeyError, ValueError, ProtocolError) as exc:
            errors.append(f"edge receiver rejected rtp_h264[{index}]: {exc}")
    return errors, True


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.write:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(expected_fixture(), indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {FIXTURE.relative_to(ROOT)}")
        return 0
    if not FIXTURE.exists():
        print(f"missing fixture: {FIXTURE.relative_to(ROOT)}", file=sys.stderr)
        return 1
    try:
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid fixture: {exc}", file=sys.stderr)
        return 1
    errors = validate(data)
    edge_errors, edge_checked = validate_with_edge_receiver(data)
    errors.extend(edge_errors)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    sizes = [len(compact_json(packet)) for packet in data["telemetry_json"]]
    edge_result = ", edge decoder compatible" if edge_checked else ""
    print(
        f"Protocol fixtures passed: {len(sizes)} JSON datagrams "
        f"(max {max(sizes)} bytes), {len(data['rtp_h264'])} RTP/H.264 packets"
        f"{edge_result}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
