from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any


TELEMETRY_TYPES = {"telemetry", "flight", "rtk", "gimbal", "health", "hello"}
FRAME_TYPES = {"frame_meta", "video_au"}
CLOCK_TYPES = {"clock_ping", "clock_pong"}


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Packet:
    version: int
    packet_type: str
    session: str
    stream: str
    sequence: int
    android_mono_ns: int
    body: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class RtpPacket:
    version: int
    payload_type: int
    marker: bool
    sequence: int
    timestamp: int
    ssrc: int
    header_size: int
    payload_size: int


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolError(f"{name} must be an integer >= {minimum}")
    return value


def _string(value: Any, name: str, max_length: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ProtocolError(f"{name} must be a non-empty string <= {max_length} characters")
    return value


def _finite_tree(value: Any, path: str = "body") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError(f"{path} contains a non-finite number")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ProtocolError(f"{path} contains a non-string key")
            _finite_tree(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _finite_tree(child, f"{path}[{index}]")


def decode_json_packet(data: bytes, expected_version: int, max_bytes: int = 1200) -> Packet:
    if not data or len(data) > max_bytes:
        raise ProtocolError(f"datagram size must be 1..{max_bytes} bytes")
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("packet root must be an object")

    version = _integer(obj.get("v"), "v", 1)
    if version != expected_version:
        raise ProtocolError(f"unsupported protocol version {version}; expected {expected_version}")
    packet_type = _string(obj.get("type"), "type", 32)
    if packet_type not in TELEMETRY_TYPES | FRAME_TYPES | CLOCK_TYPES:
        raise ProtocolError(f"unsupported packet type {packet_type!r}")
    session = _string(obj.get("session", obj.get("session_id")), "session")
    stream = _string(
        obj.get("stream", obj.get("feed", obj.get("source", packet_type))),
        "stream",
        64,
    )
    seq_name = "frame_seq" if packet_type in FRAME_TYPES else "seq"
    sequence = _integer(obj.get(seq_name, obj.get("seq")), seq_name)

    if packet_type in FRAME_TYPES:
        first_ns = _integer(
            obj.get("au_first_byte_rx_mono_ns", obj.get("android_mono_ns")),
            "au_first_byte_rx_mono_ns",
            1,
        )
        complete_ns = _integer(
            obj.get("au_complete_rx_mono_ns", first_ns),
            "au_complete_rx_mono_ns",
            first_ns,
        )
        _integer(obj.get("rtp_ssrc"), "rtp_ssrc")
        _integer(obj.get("rtp_ts"), "rtp_ts")
        if "first_rtp_seq" in obj:
            _integer(obj["first_rtp_seq"], "first_rtp_seq")
        if "final_rtp_seq" in obj:
            _integer(obj["final_rtp_seq"], "final_rtp_seq")
        android_mono_ns = first_ns
        body = {key: value for key, value in obj.items() if key not in _ENVELOPE_FIELDS}
        body["au_complete_rx_mono_ns"] = complete_ns
    elif packet_type in CLOCK_TYPES:
        t0 = _integer(obj.get("t0_edge_send_mono_ns"), "t0_edge_send_mono_ns", 1)
        if packet_type == "clock_pong":
            t1 = _integer(obj.get("t1_android_rx_mono_ns"), "t1_android_rx_mono_ns", 1)
            t2 = _integer(obj.get("t2_android_tx_mono_ns"), "t2_android_tx_mono_ns", t1)
            android_mono_ns = t1
        else:
            android_mono_ns = t0
        body = {key: value for key, value in obj.items() if key not in _ENVELOPE_FIELDS}
    else:
        android_mono_ns = _integer(
            obj.get("rx_mono_ns", obj.get("android_mono_ns")), "rx_mono_ns", 1
        )
        if "valid" in obj and not isinstance(obj["valid"], bool):
            raise ProtocolError("valid must be boolean")
        body = obj.get("data")
        if body is None:
            body = {key: value for key, value in obj.items() if key not in _ENVELOPE_FIELDS}
        if not isinstance(body, dict):
            raise ProtocolError("data must be an object")

    _finite_tree(body)
    return Packet(version, packet_type, session, stream, sequence, android_mono_ns, body, obj)


_ENVELOPE_FIELDS = {
    "v", "type", "session", "session_id", "stream", "feed", "source", "seq",
    "frame_seq", "rx_mono_ns", "android_mono_ns", "rx_utc_ns", "android_utc_ns", "data",
}


def parse_rtp_packet(data: bytes, expected_payload_type: int | None = None) -> RtpPacket:
    if len(data) < 12:
        raise ProtocolError("RTP packet is shorter than the fixed header")
    first, second = data[0], data[1]
    version = first >> 6
    if version != 2:
        raise ProtocolError(f"unsupported RTP version {version}")
    padding = bool(first & 0x20)
    extension = bool(first & 0x10)
    csrc_count = first & 0x0F
    payload_type = second & 0x7F
    if expected_payload_type is not None and payload_type != expected_payload_type:
        raise ProtocolError(
            f"unexpected RTP payload type {payload_type}; expected {expected_payload_type}"
        )
    header_size = 12 + 4 * csrc_count
    if len(data) < header_size:
        raise ProtocolError("truncated RTP CSRC list")
    if extension:
        if len(data) < header_size + 4:
            raise ProtocolError("truncated RTP extension header")
        words = int.from_bytes(data[header_size + 2 : header_size + 4], "big")
        header_size += 4 + words * 4
        if len(data) < header_size:
            raise ProtocolError("truncated RTP extension data")
    padding_size = data[-1] if padding else 0
    if padding and (padding_size == 0 or header_size + padding_size > len(data)):
        raise ProtocolError("invalid RTP padding")
    payload_size = len(data) - header_size - padding_size
    if payload_size <= 0:
        raise ProtocolError("RTP packet has no payload")
    return RtpPacket(
        version=version,
        payload_type=payload_type,
        marker=bool(second & 0x80),
        sequence=int.from_bytes(data[2:4], "big"),
        timestamp=int.from_bytes(data[4:8], "big"),
        ssrc=int.from_bytes(data[8:12], "big"),
        header_size=header_size,
        payload_size=payload_size,
    )
