"""Narrow, atomic mapper path settings for the local dashboard."""

from __future__ import annotations

import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable


ALLOWED_KEYS = frozenset({"min_path_spacing_m", "max_history_points"})
DEFAULT_VALUES = {"min_path_spacing_m": 0.15, "max_history_points": 5000}
_SETTING = re.compile(r"^\s*(min_path_spacing_m|max_history_points):\s*(\S+)\s*$")


def validate(values: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, dict) or set(values) != ALLOWED_KEYS:
        raise ValueError("mapper configuration must contain only the supported controls")
    spacing = values["min_path_spacing_m"]
    history = values["max_history_points"]
    if isinstance(spacing, bool) or not isinstance(spacing, (int, float)) or not math.isfinite(spacing) or spacing < 0:
        raise ValueError("min_path_spacing_m must be a finite non-negative number")
    if isinstance(history, bool) or not isinstance(history, int) or history < 0:
        raise ValueError("max_history_points must be a non-negative integer")
    return {"min_path_spacing_m": float(spacing), "max_history_points": history}


def render(values: dict[str, Any]) -> str:
    values = validate(values)
    return (
        "# Generated locally by DJI Transport Edge dashboard. Ignored by Git.\n"
        "/drone_localization_node:\n  ros__parameters:\n"
        f"    min_path_spacing_m: {values['min_path_spacing_m']:g}\n"
        f"    max_history_points: {values['max_history_points']}\n"
    )


def load(path: str | Path) -> dict[str, Any] | None:
    """Read only the two keys from a dashboard-generated runtime overlay."""
    target = Path(path)
    if not target.is_file():
        return None
    values: dict[str, Any] = {}
    for line in target.read_text(encoding="utf-8").splitlines():
        match = _SETTING.fullmatch(line)
        if match is None:
            continue
        key, value = match.groups()
        values[key] = int(value) if key == "max_history_points" else float(value)
    return validate(values)


def atomic_write(path: str | Path, values: dict[str, Any]) -> Path:
    target = Path(path)
    payload = render(values)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return target


def save_requested_config(
    values: dict[str, Any],
    config_path: str | Path,
    restart_request_path: str | Path,
    *,
    restart: bool,
    request_stop: Callable[[], None],
) -> dict[str, Any]:
    """Persist path controls and request a single managed restart when asked."""
    target = atomic_write(config_path, values)
    result = {"status": "saved; applies on next restart", "source": str(target), "restarting": False}
    if not restart:
        return result
    marker = Path(restart_request_path)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("dashboard mapper restart requested\n", encoding="utf-8")
    except OSError as error:
        raise OSError(f"mapper configuration saved but restart was not requested: {error}") from error
    request_stop()
    return {**result, "status": "saved; restarting managed stack", "restarting": True}
