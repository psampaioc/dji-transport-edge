from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
import time
from typing import Any

from .clock import ClockMapper
from .protocol import Packet, video_au_identity
from .synchronization import TemporalCorrelation


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


class IngressMetrics:
    """Small post-network counters for existing JSON datagram boundaries."""

    def __init__(self, categories: tuple[str, ...]) -> None:
        self._lock = Lock()
        self._values = {
            category: {"datagrams_received": 0, "packets_valid": 0, "packets_rejected": 0, "last_datagram_mono_ns": None}
            for category in categories
        }

    def observe(self, category: str, receive_mono_ns: int) -> None:
        with self._lock:
            value = self._values[category]
            value["datagrams_received"] += 1
            value["last_datagram_mono_ns"] = receive_mono_ns

    def accept(self, category: str) -> None:
        with self._lock:
            self._values[category]["packets_valid"] += 1

    def reject(self, category: str) -> None:
        with self._lock:
            self._values[category]["packets_rejected"] += 1

    def snapshot(self, now_mono_ns: int | None = None) -> dict[str, dict[str, int | float | None]]:
        now = time.monotonic_ns() if now_mono_ns is None else now_mono_ns
        with self._lock:
            values = deepcopy(self._values)
        for value in values.values():
            received = value.pop("last_datagram_mono_ns")
            value["last_datagram_age_s"] = None if received is None else max(0.0, (now - received) / 1_000_000_000)
        return values


class LatestState:
    """Thread-safe latest complete Android state, independent of ROS."""

    def __init__(self, clock_mapper: ClockMapper) -> None:
        self._clock_mapper, self._lock = clock_mapper, Lock()
        self._session: str | None = None
        self._sources: dict[str, dict] = {}
        self._history: dict[str, deque[dict]] = {}
        self._frames: dict[str, dict] = {}
        self._partial: dict[tuple[str, str, int], dict] = {}
        self.correlation = TemporalCorrelation()
        self._transport: dict[str, Any] = {"accepted_packets": 0, "rejected_packets": 0, "duplicates": 0, "out_of_order": 0, "sequence_gaps": 0}

    def update_packet(self, packet: Packet, edge_receive_ns: int, remote: tuple[str, int], sequence: SequenceResult) -> bool:
        record = {"type": packet.packet_type, "stream": packet.stream, "sequence": packet.sequence, "android_mono_ns": packet.android_mono_ns, "mapped_edge_mono_ns": self._clock_mapper.android_to_edge(packet.android_mono_ns), "edge_receive_mono_ns": edge_receive_ns, "remote": f"{remote[0]}:{remote[1]}", "sequence_status": sequence.disposition, "sequence_gap": sequence.gap, "data": deepcopy(packet.body)}
        record["network_age_ns"] = None if record["mapped_edge_mono_ns"] is None else edge_receive_ns - record["mapped_edge_mono_ns"]
        with self._lock:
            if self._session is not None and self._session != packet.session:
                self._sources.clear(); self._history.clear(); self._frames.clear(); self._partial.clear()
            self._session = packet.session
            self.correlation.start_session(packet.session)
            self._transport["accepted_packets"] += 1
            if sequence.disposition == "duplicate": self._transport["duplicates"] += 1
            elif sequence.disposition == "out_of_order": self._transport["out_of_order"] += 1
            elif sequence.disposition == "gap": self._transport["sequence_gaps"] += sequence.gap
            if not sequence.is_newest:
                return False
            if packet.packet_type in {"frame_meta", "video_au"}:
                if packet.packet_type == "video_au":
                    try:
                        identity = video_au_identity(packet)
                    except ValueError:
                        self._transport["rejected_packets"] += 1
                        return False
                    self.correlation.add_access_unit(identity)
                    record["identity"] = {
                        "session": identity.session, "feed": identity.feed, "frame_seq": identity.frame_seq,
                        "rtp_ssrc": identity.rtp_ssrc, "rtp_ts": identity.rtp_ts,
                        "android_first_byte_mono_ns": identity.android_first_byte_mono_ns,
                        "android_complete_mono_ns": identity.android_complete_mono_ns,
                        "dji_source_timestamp_ns": identity.dji_source_timestamp_ns,
                        "dji_timestamp_source": identity.dji_timestamp_source,
                    }
                self._frames[packet.stream] = record
                return True
            body = packet.body
            if {"sample_sequence", "chunk_index", "chunk_count", "fields"} <= body.keys():
                values = (body["sample_sequence"], body["chunk_index"], body["chunk_count"])
                if not all(isinstance(value, int) and not isinstance(value, bool) for value in values) or body["chunk_count"] < 1 or body["chunk_index"] < 0 or body["chunk_index"] >= body["chunk_count"] or not isinstance(body["fields"], dict):
                    self._transport["rejected_packets"] += 1
                    return False
                key = (packet.session, packet.stream, body["sample_sequence"])
                for old_key in list(self._partial):
                    if old_key[:2] == key[:2] and old_key[2] < key[2]: del self._partial[old_key]
                partial = self._partial.setdefault(key, {"chunk_count": body["chunk_count"], "chunks": {}, "component_index": body.get("component_index", 0)})
                if partial["chunk_count"] != body["chunk_count"]:
                    del self._partial[key]; self._transport["rejected_packets"] += 1; return False
                partial["chunks"][body["chunk_index"]] = deepcopy(body["fields"])
                if len(partial["chunks"]) != partial["chunk_count"]:
                    return False
                fields: dict[str, Any] = {}
                for index in range(partial["chunk_count"]): fields.update(partial["chunks"][index])
                record["data"] = {"sample_sequence": body["sample_sequence"], "component_index": partial["component_index"], "fields": fields}
                del self._partial[key]
            self._sources[packet.stream] = record
            self._history.setdefault(packet.stream, deque(maxlen=512)).append(deepcopy(record))
            self.correlation.add_telemetry(packet.packet_type, deepcopy(record))
            return True

    def reject(self) -> None:
        with self._lock: self._transport["rejected_packets"] += 1

    def associate_frame(self, feed: str, rtp_ssrc: int, rtp_ts: int) -> tuple[dict, dict] | None:
        """Return AU identity plus frame-time navigation, or nothing when uncertain."""
        with self._lock:
            identity = self.correlation.identity_for_rtp(feed, rtp_ssrc, rtp_ts)
            if identity is None:
                return None
            return ({
                "session": identity.session, "feed": identity.feed, "frame_seq": identity.frame_seq,
                "rtp_ssrc": identity.rtp_ssrc, "rtp_ts": identity.rtp_ts,
                "android_first_byte_mono_ns": identity.android_first_byte_mono_ns,
                "android_complete_mono_ns": identity.android_complete_mono_ns,
                "dji_source_timestamp_ns": identity.dji_source_timestamp_ns,
                "dji_timestamp_source": identity.dji_timestamp_source,
            }, self.correlation.associate_android_time(identity.android_complete_mono_ns))

    def snapshot(self) -> dict:
        now = time.monotonic_ns()
        with self._lock:
            sources, frames, transport, session = deepcopy(self._sources), deepcopy(self._frames), deepcopy(self._transport), self._session
            history = {key: list(value) for key, value in self._history.items()}
            correlation = self.correlation.snapshot()
        for record in list(sources.values()) + list(frames.values()): record["edge_receive_age_ns"] = now - record["edge_receive_mono_ns"]
        for frame in frames.values():
            associations = {}
            for source, records in history.items():
                prior = next((record for record in reversed(records) if record["android_mono_ns"] <= frame["android_mono_ns"]), None)
                associations[source] = {"sequence": None if prior is None else prior["sequence"], "age_ns": None if prior is None else frame["android_mono_ns"] - prior["android_mono_ns"], "causal": prior is not None, "method": "latest_previous" if prior else "no_previous_sample"}
            frame["telemetry_associations"] = associations
        typed = lambda name: [value for value in sources.values() if value["type"] == name]
        flight, rtk, gimbal, health = typed("flight"), typed("rtk"), typed("gimbal"), typed("health")
        return {"schema_version": 1, "session": session, "edge_mono_ns": now, "flight": flight[-1] if flight else None, "rtk": rtk[-1] if rtk else None, "gimbal": gimbal[-1] if gimbal else None, "health": health[-1] if health else None, "sources": sources, "video_frames": frames, "clock": self._clock_mapper.estimate(), "transport": transport, "correlation": correlation}
