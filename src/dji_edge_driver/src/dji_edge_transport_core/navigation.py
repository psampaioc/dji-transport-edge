"""Normalize latest Android state into a map-ready navigation sample."""

from __future__ import annotations

import math
from typing import Any


def triggers_navigation(packet_type: str) -> bool:
    """Publish the map snapshot only from the bounded flight cadence."""
    return packet_type == "flight"


def _unwrap(fields: dict[str, Any], key: str, default: Any = None) -> Any:
    value = fields.get(key, default)
    return value.get("value", default) if isinstance(value, dict) else value


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def build_navigation(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Return RTK-preferred navigation, with continuous GPS fallback."""
    flight = snapshot.get("flight") or {}
    rtk = snapshot.get("rtk") or {}
    gimbal = snapshot.get("gimbal") or {}
    flight_fields = flight.get("data", {}).get("fields", {})
    rtk_fields = rtk.get("data", {}).get("fields", {})
    gimbal_fields = gimbal.get("data", {}).get("fields", {})

    rtk_latitude = _unwrap(rtk_fields, "fusion.latitude_deg")
    rtk_longitude = _unwrap(rtk_fields, "fusion.longitude_deg")
    rtk_valid = bool(_unwrap(rtk_fields, "is_being_used", False)) and _finite(rtk_latitude) and _finite(rtk_longitude)
    if rtk_valid:
        latitude, longitude, source, timing = rtk_latitude, rtk_longitude, "rtk", rtk
    else:
        latitude = _unwrap(flight_fields, "aircraft.latitude_deg")
        longitude = _unwrap(flight_fields, "aircraft.longitude_deg")
        source, timing = "gps_fallback", flight
    if not (_finite(latitude) and _finite(longitude)):
        return None

    mapped = timing.get("mapped_edge_mono_ns")
    received = timing.get("edge_receive_mono_ns", 0)
    transport_age_s = None if mapped is None else (received - mapped) / 1_000_000_000
    return {
        "latitude_deg": float(latitude),
        "longitude_deg": float(longitude),
        "altitude_m": float(_unwrap(flight_fields, "aircraft.altitude_m", 0.0) or 0.0),
        "heading_deg": float(_unwrap(flight_fields, "heading_deg", 0.0) or 0.0),
        "position_source": source,
        "position_valid": True,
        "rtk_valid": rtk_valid,
        "gimbal_pitch_valid": _finite(_unwrap(gimbal_fields, "attitude.pitch_deg")),
        "gimbal_pitch_deg": float(_unwrap(gimbal_fields, "attitude.pitch_deg", 0.0) or 0.0),
        "gimbal_android_mono_ns": int(gimbal.get("android_mono_ns", 0) or 0),
        "session": snapshot.get("session") or timing.get("session", ""),
        "android_mono_ns": int(timing.get("android_mono_ns", 0) or 0),
        "edge_receive_mono_ns": int(received or 0),
        "transport_age_s": transport_age_s,
    }
