"""Bounded cross-thread handoff for decoded video frames."""

from __future__ import annotations

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
