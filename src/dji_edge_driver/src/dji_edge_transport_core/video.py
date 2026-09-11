"""Bounded cross-thread handoff for decoded video frames."""

from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Generic, TypeVar


T = TypeVar("T")


class LatestFrameBuffer(Generic[T]):
    """A one-item buffer that replaces stale work instead of queuing latency."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._frame: T | None = None

    def put(self, frame: T) -> bool:
        """Store frame and return whether an older frame was discarded."""
        with self._lock:
            replaced = self._frame is not None
            self._frame = frame
            return replaced

    def take(self) -> T | None:
        with self._lock:
            frame, self._frame = self._frame, None
            return frame

    def clear(self) -> None:
        with self._lock:
            self._frame = None


class RtpPtsBinding:
    """Bounded internal GStreamer PTS-to-RTP mapping; never source time."""

    def __init__(self, max_entries: int = 256) -> None:
        self._lock = Lock()
        self._entries: dict[int, tuple[int, int] | None] = {}
        self._order: deque[int] = deque(maxlen=max_entries)
        self._ambiguous = self._missing = 0

    def observe(self, pts_ns: int | None, rtp_ssrc: int, rtp_ts: int) -> bool:
        if pts_ns is None or pts_ns < 0:
            with self._lock:
                self._missing += 1
            return False
        with self._lock:
            identity = (rtp_ssrc, rtp_ts)
            if pts_ns in self._entries:
                existing = self._entries[pts_ns]
                if existing != identity:
                    self._entries[pts_ns] = None
                    self._ambiguous += 1
                    return False
                return True
            if pts_ns not in self._entries and len(self._order) == self._order.maxlen:
                self._entries.pop(self._order.popleft(), None)
            self._order.append(pts_ns)
            self._entries[pts_ns] = identity
            return True

    def resolve(self, pts_ns: int | None) -> tuple[int, int] | None:
        if pts_ns is None or pts_ns < 0:
            with self._lock:
                self._missing += 1
            return None
        with self._lock:
            identity = self._entries.pop(pts_ns, None)
            if identity is None:
                self._missing += 1
            return identity

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"pending": len(self._entries), "ambiguous": self._ambiguous, "missing": self._missing}
