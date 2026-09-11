"""Small, source-time-only temporal association for frame context."""

from __future__ import annotations

from collections import deque
import math
from typing import Any

from .protocol import VideoAuIdentity


def _value(fields: dict[str, Any], name: str) -> Any:
    value = fields.get(name)
    return value.get("value") if isinstance(value, dict) else value


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _interpolate_angle(first: float, second: float, ratio: float) -> float:
    return (first + ((second - first + 180.0) % 360.0 - 180.0) * ratio) % 360.0


class TemporalCorrelation:
    """Keeps a bounded navigation history keyed solely by Android monotonic time."""

    def __init__(self, max_samples: int = 512, max_gap_ns: int = 750_000_000, max_access_units: int = 256) -> None:
        self.max_gap_ns = max_gap_ns
        self._session: str | None = None
        self._history: dict[str, deque[dict[str, Any]]] = {
            name: deque(maxlen=max_samples) for name in ("flight", "rtk", "gimbal")
        }
        self._access_units: dict[tuple[str, int, int], VideoAuIdentity | None] = {}
        self._access_unit_order: deque[tuple[str, int, int]] = deque(maxlen=max_access_units)

    def start_session(self, session: str) -> None:
        if self._session == session:
            return
        self._session = session
        for records in self._history.values():
            records.clear()
        self._access_units.clear()
        self._access_unit_order.clear()

    def add_access_unit(self, identity: VideoAuIdentity) -> bool:
        """Index only a bounded recent AU window; ambiguous keys fail closed."""
        if identity.session != self._session:
            return False
        key = (identity.feed, identity.rtp_ssrc, identity.rtp_ts)
        previous = self._access_units.get(key)
        if previous is not None:
            if previous == identity:
                return False
            self._access_units[key] = None
            return False
        if key not in self._access_units and len(self._access_unit_order) == self._access_unit_order.maxlen:
            expired = self._access_unit_order.popleft()
            self._access_units.pop(expired, None)
        self._access_unit_order.append(key)
        self._access_units[key] = identity
        return True

    def identity_for_rtp(self, feed: str, rtp_ssrc: int, rtp_ts: int) -> VideoAuIdentity | None:
        return self._access_units.get((feed, rtp_ssrc, rtp_ts))

    def add_telemetry(self, packet_type: str, record: dict[str, Any]) -> None:
        records = self._history.get(packet_type)
        if records is None:
            return
        timestamp = record.get("android_mono_ns")
        if not isinstance(timestamp, int) or timestamp < 1:
            return
        if records and timestamp <= records[-1]["android_mono_ns"]:
            return
        records.append(record)

    def associate_android_time(self, android_mono_ns: int) -> dict[str, Any]:
        flight, flight_quality, flight_offset = self._sample("flight", android_mono_ns, self._flight_values)
        if flight is None:
            return self._unavailable("flight sample unavailable")
        gimbal, gimbal_quality, _gimbal_offset = self._sample("gimbal", android_mono_ns, self._gimbal_values)
        rtk, rtk_quality, rtk_offset = self._sample("rtk", android_mono_ns, self._rtk_values)
        if rtk is not None and rtk["valid"]:
            latitude, longitude, position_source = rtk["latitude_deg"], rtk["longitude_deg"], "rtk"
            position_offset = rtk_offset
            position_quality = rtk_quality
        else:
            latitude, longitude, position_source = flight["latitude_deg"], flight["longitude_deg"], "gps_fallback"
            position_offset = flight_offset
            position_quality = flight_quality
        if latitude is None or longitude is None or flight["altitude_m"] is None or flight["heading_deg"] is None:
            return self._unavailable("position or heading unavailable")
        qualities = {position_quality, flight_quality}
        quality = "interpolated" if "interpolated" in qualities else "nearest"
        return {
            "association_quality": quality,
            "association_reason": quality,
            "navigation_time_offset_ns": position_offset,
            "position_valid": True,
            "position_source": position_source,
            "rtk_valid": position_source == "rtk",
            "latitude_deg": latitude,
            "longitude_deg": longitude,
            "altitude_m": flight["altitude_m"],
            "heading_deg": flight["heading_deg"],
            "flight_android_mono_ns": flight["timestamp_ns"],
            "rtk_android_mono_ns": 0 if rtk is None else rtk["timestamp_ns"],
            "gimbal_pitch_valid": gimbal is not None and gimbal["pitch_deg"] is not None,
            "gimbal_pitch_deg": 0.0 if gimbal is None or gimbal["pitch_deg"] is None else gimbal["pitch_deg"],
            "gimbal_android_mono_ns": 0 if gimbal is None else gimbal["timestamp_ns"],
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "session": self._session,
            "history_sizes": {name: len(records) for name, records in self._history.items()},
            "access_units_indexed": sum(identity is not None for identity in self._access_units.values()),
            "access_units_ambiguous": sum(identity is None for identity in self._access_units.values()),
        }

    def _sample(self, source: str, target_ns: int, extractor):
        records = list(self._history[source])
        if not records:
            return None, "unavailable", 0
        before = next((record for record in reversed(records) if record["android_mono_ns"] <= target_ns), None)
        after = next((record for record in records if record["android_mono_ns"] >= target_ns), None)
        if before is not None and after is not None and before is not after:
            before_values, after_values = extractor(before), extractor(after)
            gap = after["android_mono_ns"] - before["android_mono_ns"]
            if gap <= self.max_gap_ns:
                ratio = (target_ns - before["android_mono_ns"]) / gap
                return self._interpolate(source, before_values, after_values, ratio, target_ns), "interpolated", 0
        nearest = min(records, key=lambda item: abs(item["android_mono_ns"] - target_ns))
        offset = nearest["android_mono_ns"] - target_ns
        if abs(offset) > self.max_gap_ns:
            return None, "unavailable", offset
        return extractor(nearest), "nearest", offset

    @staticmethod
    def _fields(record: dict[str, Any]) -> dict[str, Any]:
        return record.get("data", {}).get("fields", {})

    def _flight_values(self, record: dict[str, Any]) -> dict[str, Any]:
        fields = self._fields(record)
        return {
            "timestamp_ns": record["android_mono_ns"],
            "latitude_deg": self._float(_value(fields, "aircraft.latitude_deg")),
            "longitude_deg": self._float(_value(fields, "aircraft.longitude_deg")),
            "altitude_m": self._float(_value(fields, "aircraft.altitude_m")),
            "heading_deg": self._float(_value(fields, "heading_deg")),
        }

    def _rtk_values(self, record: dict[str, Any]) -> dict[str, Any]:
        fields = self._fields(record)
        latitude, longitude = self._float(_value(fields, "fusion.latitude_deg")), self._float(_value(fields, "fusion.longitude_deg"))
        return {"timestamp_ns": record["android_mono_ns"], "latitude_deg": latitude, "longitude_deg": longitude, "valid": bool(_value(fields, "is_being_used")) and latitude is not None and longitude is not None}

    def _gimbal_values(self, record: dict[str, Any]) -> dict[str, Any]:
        return {"timestamp_ns": record["android_mono_ns"], "pitch_deg": self._float(_value(self._fields(record), "attitude.pitch_deg"))}

    @staticmethod
    def _float(value: Any) -> float | None:
        return float(value) if _finite(value) else None

    def _interpolate(self, source: str, first: dict[str, Any], second: dict[str, Any], ratio: float, target_ns: int) -> dict[str, Any]:
        if source == "rtk" and not (first["valid"] and second["valid"]):
            return {"timestamp_ns": target_ns, "latitude_deg": None, "longitude_deg": None, "valid": False}
        result = {"timestamp_ns": target_ns}
        for key in set(first) & set(second):
            if key == "timestamp_ns":
                continue
            if key == "valid":
                result[key] = bool(first[key] and second[key])
                continue
            if first[key] is None or second[key] is None:
                result[key] = None
            elif key == "heading_deg":
                result[key] = _interpolate_angle(first[key], second[key], ratio)
            else:
                result[key] = first[key] + (second[key] - first[key]) * ratio
        return result

    @staticmethod
    def _unavailable(reason: str) -> dict[str, Any]:
        return {
            "association_quality": "unavailable", "association_reason": reason,
            "navigation_time_offset_ns": 0, "position_valid": False,
            "position_source": "unknown", "rtk_valid": False,
            "latitude_deg": 0.0, "longitude_deg": 0.0, "altitude_m": 0.0, "heading_deg": 0.0,
            "flight_android_mono_ns": 0, "rtk_android_mono_ns": 0,
            "gimbal_pitch_valid": False, "gimbal_pitch_deg": 0.0, "gimbal_android_mono_ns": 0,
        }
