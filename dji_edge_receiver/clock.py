from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from threading import Lock


@dataclass(frozen=True)
class ClockSample:
    t0_edge_send_ns: int
    t1_android_receive_ns: int
    t2_android_send_ns: int
    t3_edge_receive_ns: int
    offset_android_minus_edge_ns: float
    round_trip_ns: int

    def as_dict(self) -> dict:
        return asdict(self)


class ClockMapper:
    """NTP-style Android monotonic to edge monotonic clock mapper.

    Offset is Android minus edge. Mapping Android timestamps to the edge clock
    therefore subtracts the estimated offset. The estimate uses the median of
    the lowest-RTT samples to reduce asymmetric queueing noise.
    """

    def __init__(self, max_samples: int = 64, best_sample_count: int = 8) -> None:
        self._max_samples = max_samples
        self._best_sample_count = best_sample_count
        self._samples: list[ClockSample] = []
        self._lock = Lock()

    def add_exchange(self, t0: int, t1: int, t2: int, t3: int) -> ClockSample:
        if min(t0, t1, t2, t3) <= 0 or t3 < t0 or t2 < t1:
            raise ValueError("invalid clock exchange ordering")
        rtt = (t3 - t0) - (t2 - t1)
        if rtt < 0:
            raise ValueError("clock exchange produced a negative network RTT")
        offset = ((t1 - t0) + (t2 - t3)) / 2.0
        sample = ClockSample(t0, t1, t2, t3, offset, rtt)
        with self._lock:
            self._samples.append(sample)
            self._samples = self._samples[-self._max_samples :]
        return sample

    def estimate(self) -> dict:
        with self._lock:
            samples = list(self._samples)
        if not samples:
            return {"ready": False, "sample_count": 0}
        best = sorted(samples, key=lambda item: item.round_trip_ns)[: self._best_sample_count]
        offset = median(item.offset_android_minus_edge_ns for item in best)
        return {
            "ready": True,
            "sample_count": len(samples),
            "offset_android_minus_edge_ns": offset,
            "best_rtt_ns": min(item.round_trip_ns for item in samples),
            "median_best_rtt_ns": median(item.round_trip_ns for item in best),
        }

    def android_to_edge(self, android_mono_ns: int) -> int | None:
        estimate = self.estimate()
        if not estimate["ready"]:
            return None
        return round(android_mono_ns - estimate["offset_android_minus_edge_ns"])

