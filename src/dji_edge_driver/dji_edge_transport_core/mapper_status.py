"""Bounded cache for the mapper's existing compact status topic."""

from __future__ import annotations

import json
import time


COUNTERS = frozenset({
    "accepted", "accepted_gps_fallback", "accepted_rtk", "frame_context_published",
    "frame_context_received", "frame_context_rejected", "frame_context_unavailable",
    "path_poses", "received", "rejected", "rejected_invalid", "rejected_jump",
    "rejected_outside_map", "rejected_projection",
})
REQUIRED_COUNTERS = frozenset({"accepted", "received", "path_poses"})


class MapperStatusCache:
    """Retains one valid observation; invalid input never replaces it."""

    def __init__(self) -> None:
        self.values: dict[str, int] | None = None
        self.received_mono_ns: int | None = None
        self.error: str | None = None

    def observe(self, data: str, received_mono_ns: int | None = None) -> None:
        try:
            if len(data) > 4096:
                raise ValueError("mapper status exceeds dashboard limit")
            payload = json.loads(data)
            if not isinstance(payload, dict) or not REQUIRED_COUNTERS.issubset(payload) or any(
                key not in COUNTERS or isinstance(value, bool) or not isinstance(value, int) or value < 0
                for key, value in payload.items()
            ):
                raise ValueError("mapper status has invalid counters")
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.error = str(error)
            return
        self.values = payload
        self.received_mono_ns = time.monotonic_ns() if received_mono_ns is None else received_mono_ns
        self.error = None

    def snapshot(self, stale_after_s: float, now_mono_ns: int | None = None) -> dict[str, object]:
        now = time.monotonic_ns() if now_mono_ns is None else now_mono_ns
        age_s = None if self.received_mono_ns is None else max(0.0, (now - self.received_mono_ns) / 1e9)
        state = "invalid" if self.error else "unavailable" if self.values is None else "stale" if age_s is not None and age_s > stale_after_s else "ok"
        return {"state": state, "age_s": age_s, "values": self.values, "error": self.error}
