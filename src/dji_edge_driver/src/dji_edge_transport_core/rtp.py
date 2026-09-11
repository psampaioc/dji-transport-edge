"""Post-network RTP accounting and optional raw diagnostic capture."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import struct
from threading import Lock
import time

from .protocol import ProtocolError, RtpPacket, parse_rtp_packet
from .state import SequenceTracker


class RtpMetrics:
    """Tracks packets consumed by the direct GStreamer ingress pipeline."""

    def __init__(self, expected_payload_type: int) -> None:
        self._expected_payload_type = expected_payload_type
        self._sequence = SequenceTracker(modulus=1 << 16)
        self._lock = Lock()
        self._packets_received = 0
        self._datagrams_observed = 0
        self._bytes_received = 0
        self._packets_rejected = 0
        self._sequence_gaps = 0
        self._duplicates = 0
        self._out_of_order = 0
        self._access_units_observed = 0
        self._access_unit_bytes_total = 0
        self._access_unit_bytes_max = 0
        self._current_access_unit_bytes = 0
        self._last_rtp_timestamp: int | None = None
        self._last_datagram_mono_ns: int | None = None
        # Keep a bounded measurement window. Lifetime totals are exposed
        # separately, so they must not be divided by a short recent window.
        self._recent_access_units: deque[tuple[int, int]] = deque(maxlen=120)

    def observe(self, data: bytes | memoryview, receive_mono_ns: int | None = None) -> RtpPacket | None:
        received = time.monotonic_ns() if receive_mono_ns is None else receive_mono_ns
        with self._lock:
            self._datagrams_observed += 1
            self._last_datagram_mono_ns = received
        try:
            packet = parse_rtp_packet(data, self._expected_payload_type)
        except ProtocolError:
            with self._lock:
                self._packets_rejected += 1
            return None

        result = self._sequence.observe((packet.ssrc, packet.payload_type), packet.sequence)
        with self._lock:
            self._packets_received += 1
            self._bytes_received += len(data)
            self._last_rtp_timestamp = packet.timestamp
            if result.disposition == "duplicate":
                self._duplicates += 1
                return packet
            if result.disposition == "out_of_order":
                self._out_of_order += 1
                return packet
            if result.disposition == "gap":
                self._sequence_gaps += result.gap
            self._current_access_unit_bytes += len(data)
            if packet.marker:
                access_unit_bytes = self._current_access_unit_bytes
                self._access_units_observed += 1
                self._access_unit_bytes_total += access_unit_bytes
                self._access_unit_bytes_max = max(self._access_unit_bytes_max, access_unit_bytes)
                self._current_access_unit_bytes = 0
                self._recent_access_units.append((received, access_unit_bytes))
        return packet

    def snapshot(self, now_mono_ns: int | None = None) -> dict[str, int | float | None]:
        with self._lock:
            now = time.monotonic_ns() if now_mono_ns is None else now_mono_ns
            fps = None
            if len(self._recent_access_units) >= 2:
                duration_ns = self._recent_access_units[-1][0] - self._recent_access_units[0][0]
                if duration_ns > 0:
                    fps = (len(self._recent_access_units) - 1) * 1_000_000_000 / duration_ns
            bitrate = None
            if len(self._recent_access_units) >= 2:
                duration_ns = self._recent_access_units[-1][0] - self._recent_access_units[0][0]
                if duration_ns > 0:
                    recent_bytes = sum(size for _, size in self._recent_access_units)
                    bitrate = recent_bytes * 8 * 1_000_000_000 / duration_ns
            average = None if not self._access_units_observed else self._access_unit_bytes_total / self._access_units_observed
            return {
                "datagrams_observed": self._datagrams_observed,
                "packets_received": self._packets_received,
                "bytes_received": self._bytes_received,
                "packets_rejected": self._packets_rejected,
                "sequence_gaps": self._sequence_gaps,
                "duplicates": self._duplicates,
                "out_of_order": self._out_of_order,
                "access_units_observed": self._access_units_observed,
                "access_unit_bytes_total": self._access_unit_bytes_total,
                "access_unit_bytes_avg": average,
                "access_unit_bytes_max": self._access_unit_bytes_max,
                "estimated_fps": fps,
                "estimated_bitrate_bps": bitrate,
                "last_rtp_timestamp": self._last_rtp_timestamp,
                "last_datagram_age_s": None if self._last_datagram_mono_ns is None else max(0.0, (now - self._last_datagram_mono_ns) / 1_000_000_000),
            }


class RawRtpCapture:
    """Optional length-prefixed RTP capture for offline packet diagnostics."""

    _MAGIC = b"DJIRTP01"

    def __init__(self, path: str | Path | None) -> None:
        self._lock = Lock()
        self._stream = None
        if path is not None:
            capture_path = Path(path)
            capture_path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = capture_path.open("wb")
            self._stream.write(self._MAGIC)
            self._stream.flush()

    @property
    def enabled(self) -> bool:
        return self._stream is not None

    def write(self, data: bytes | memoryview) -> bool:
        with self._lock:
            if self._stream is None:
                return False
            self._stream.write(struct.pack(">I", len(data)))
            self._stream.write(data)
            return True

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.flush()
                self._stream.close()
                self._stream = None
