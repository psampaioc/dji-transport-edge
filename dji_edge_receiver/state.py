from __future__ import annotations

from copy import deepcopy
from collections import deque
from dataclasses import dataclass
from threading import Lock
import time
from typing import Any

from .clock import ClockMapper
from .protocol import Packet


@dataclass(frozen=True)
class SequenceResult:
    disposition: str
    gap: int = 0

    @property
    def is_newest(self) -> bool:
        return self.disposition in {"first", "ok", "gap"}


class SequenceTracker:
    def __init__(self, modulus: int | None = None) -> None:
        self._modulus = modulus
        self._latest: dict[tuple[Any, ...], int] = {}
        self._lock = Lock()

    def observe(self, key: tuple[Any, ...], sequence: int) -> SequenceResult:
        with self._lock:
            previous = self._latest.get(key)
            if previous is None:
                self._latest[key] = sequence
                return SequenceResult("first")
            if sequence == previous:
                return SequenceResult("duplicate")
            if self._modulus is None:
                if sequence < previous:
                    return SequenceResult("out_of_order")
                self._latest[key] = sequence
                gap = sequence - previous - 1
                return SequenceResult("gap" if gap else "ok", gap)

            distance = (sequence - previous) % self._modulus
            if distance == 0:
                return SequenceResult("duplicate")
            if distance >= self._modulus // 2:
                return SequenceResult("out_of_order")
            self._latest[key] = sequence
            gap = distance - 1
            return SequenceResult("gap" if gap else "ok", gap)


class LatestState:
    """Thread-safe, ROS-independent latest-state boundary."""

    def __init__(self, clock_mapper: ClockMapper) -> None:
        self._clock_mapper = clock_mapper
        self._lock = Lock()
        self._session: str | None = None
        self._sources: dict[str, dict] = {}
        self._history: dict[str, deque[dict]] = {}
        self._frames: dict[str, dict] = {}
        self._partial_samples: dict[tuple[str, str, int], dict] = {}
        self._transport: dict[str, Any] = {
            "accepted_packets": 0,
            "rejected_packets": 0,
            "duplicates": 0,
            "out_of_order": 0,
            "sequence_gaps": 0,
            "rtp_packets": 0,
            "rtp_bytes": 0,
            "rtp_rejected": 0,
            "rtp_duplicates": 0,
            "rtp_out_of_order": 0,
            "rtp_sequence_gaps": 0,
            "timestamp_regressions": 0,
        }
        self._type_stats: dict[str, dict[str, Any]] = {}

    def record_packet_size(self, packet_type: str, byte_count: int) -> None:
        with self._lock:
            stats = self._type_stats.setdefault(packet_type, {
                "datagrams": 0,
                "bytes": 0,
                "min_bytes": None,
                "max_bytes": 0,
                "complete_samples": 0,
            })
            stats["datagrams"] += 1
            stats["bytes"] += byte_count
            stats["min_bytes"] = byte_count if stats["min_bytes"] is None else min(stats["min_bytes"], byte_count)
            stats["max_bytes"] = max(stats["max_bytes"], byte_count)

    def record_complete_sample(self, packet_type: str) -> None:
        with self._lock:
            stats = self._type_stats.setdefault(packet_type, {
                "datagrams": 0, "bytes": 0, "min_bytes": None,
                "max_bytes": 0, "complete_samples": 0,
            })
            stats["complete_samples"] += 1

    def update_packet(
        self,
        packet: Packet,
        edge_receive_ns: int,
        remote: tuple[str, int],
        sequence: SequenceResult,
    ) -> None:
        mapped = self._clock_mapper.android_to_edge(packet.android_mono_ns)
        record = {
            "type": packet.packet_type,
            "stream": packet.stream,
            "sequence": packet.sequence,
            "android_mono_ns": packet.android_mono_ns,
            "mapped_edge_mono_ns": mapped,
            "edge_receive_mono_ns": edge_receive_ns,
            "network_age_ns": None if mapped is None else edge_receive_ns - mapped,
            "remote": f"{remote[0]}:{remote[1]}",
            "sequence_status": sequence.disposition,
            "sequence_gap": sequence.gap,
            "data": deepcopy(packet.body),
        }
        with self._lock:
            if self._session is not None and self._session != packet.session:
                self._sources.clear()
                self._history.clear()
                self._frames.clear()
                self._partial_samples.clear()
            self._session = packet.session
            self._transport["accepted_packets"] += 1
            if sequence.disposition == "duplicate":
                self._transport["duplicates"] += 1
            elif sequence.disposition == "out_of_order":
                self._transport["out_of_order"] += 1
            elif sequence.disposition == "gap":
                self._transport["sequence_gaps"] += sequence.gap
            elif sequence.disposition == "timestamp_regression":
                self._transport["timestamp_regressions"] += 1
            if not sequence.is_newest:
                return
            if packet.packet_type in {"frame_meta", "video_au"}:
                self._frames[packet.stream] = record
            else:
                body = packet.body
                if {"sample_sequence", "chunk_index", "chunk_count", "fields"} <= body.keys():
                    sample_sequence = body["sample_sequence"]
                    chunk_index = body["chunk_index"]
                    chunk_count = body["chunk_count"]
                    if not all(isinstance(value, int) and not isinstance(value, bool)
                               for value in (sample_sequence, chunk_index, chunk_count)):
                        self._transport["rejected_packets"] += 1
                        return
                    if chunk_count < 1 or chunk_index < 0 or chunk_index >= chunk_count \
                            or not isinstance(body["fields"], dict):
                        self._transport["rejected_packets"] += 1
                        return
                    key = (packet.session, packet.stream, sample_sequence)
                    # A native callback supersedes incomplete older callbacks from the same stream.
                    for old_key in list(self._partial_samples):
                        if old_key[:2] == key[:2] and old_key[2] < sample_sequence:
                            del self._partial_samples[old_key]
                    partial = self._partial_samples.setdefault(key, {
                        "chunk_count": chunk_count,
                        "chunks": {},
                        "component_index": body.get("component_index", 0),
                    })
                    if partial["chunk_count"] != chunk_count:
                        del self._partial_samples[key]
                        self._transport["rejected_packets"] += 1
                        return
                    partial["chunks"][chunk_index] = deepcopy(body["fields"])
                    if len(partial["chunks"]) != chunk_count:
                        return
                    merged_fields = {}
                    for index in range(chunk_count):
                        merged_fields.update(partial["chunks"][index])
                    record["data"] = {
                        "sample_sequence": sample_sequence,
                        "component_index": partial["component_index"],
                        "fields": merged_fields,
                    }
                    del self._partial_samples[key]
                stats = self._type_stats.setdefault(packet.packet_type, {
                    "datagrams": 0, "bytes": 0, "min_bytes": None,
                    "max_bytes": 0, "complete_samples": 0,
                })
                stats["complete_samples"] += 1
                source_key = packet.stream
                self._sources[source_key] = record
                history = self._history.setdefault(source_key, deque(maxlen=512))
                history.append(deepcopy(record))

    def reject(self) -> None:
        with self._lock:
            self._transport["rejected_packets"] += 1

    def update_rtp(
        self,
        byte_count: int,
        rejected: bool = False,
        disposition: str = "ok",
        gap: int = 0,
    ) -> None:
        with self._lock:
            if rejected:
                self._transport["rtp_rejected"] += 1
            else:
                self._transport["rtp_packets"] += 1
                self._transport["rtp_bytes"] += byte_count
                self._transport["rtp_sequence_gaps"] += gap
                if disposition == "duplicate":
                    self._transport["rtp_duplicates"] += 1
                elif disposition == "out_of_order":
                    self._transport["rtp_out_of_order"] += 1

    def snapshot(self) -> dict:
        now = time.monotonic_ns()
        with self._lock:
            sources = deepcopy(self._sources)
            frames = deepcopy(self._frames)
            transport = deepcopy(self._transport)
            type_stats = deepcopy(self._type_stats)
            session = self._session
        for record in list(sources.values()) + list(frames.values()):
            record["edge_receive_age_ns"] = now - record["edge_receive_mono_ns"]

        with self._lock:
            history = {key: list(values) for key, values in self._history.items()}

        for frame in frames.values():
            associations = {}
            for source_name, records in history.items():
                source = next(
                    (record for record in reversed(records)
                     if record["android_mono_ns"] <= frame["android_mono_ns"]),
                    None,
                )
                if source is None:
                    associations[source_name] = {
                        "sequence": None,
                        "age_ns": None,
                        "causal": False,
                        "method": "no_previous_sample",
                    }
                    continue
                age_ns = frame["android_mono_ns"] - source["android_mono_ns"]
                associations[source_name] = {
                    "sequence": source["sequence"],
                    "age_ns": age_ns,
                    "causal": True,
                    "method": "latest_previous",
                }
            frame["telemetry_associations"] = associations

        # Named views are convenient for detection and a future ROS adapter;
        # `sources` remains authoritative and supports arbitrary new sources.
        flight = [v for v in sources.values() if v["type"] == "flight"]
        rtk = [v for v in sources.values() if v["type"] == "rtk"]
        gimbals = [v for v in sources.values() if v["type"] == "gimbal"]
        health = [v for v in sources.values() if v["type"] == "health"]
        return {
            "schema_version": 1,
            "session": session,
            "edge_mono_ns": now,
            "flight": flight[-1] if flight else None,
            "rtk": rtk[-1] if rtk else None,
            "gimbals": gimbals,
            "health": health[-1] if health else None,
            "sources": sources,
            "video_frames": frames,
            "clock": self._clock_mapper.estimate(),
            "transport": transport,
            "packet_stats": type_stats,
        }
