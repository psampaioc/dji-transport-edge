"""Validated, atomic local dashboard configuration; never a generic editor."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import tempfile
from typing import Any


ALLOWED_KEYS = frozenset({"android_clock_host", "capture_rtp", "preview_windows"})
_HOSTNAME = re.compile(r"(?=.{1,253}\Z)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\Z")


def validate(values: dict[str, Any]) -> dict[str, Any]:
    if set(values) != ALLOWED_KEYS:
        raise ValueError("configuration must contain only the supported controls")
    host = values["android_clock_host"]
    if not isinstance(host, str):
        raise ValueError("android_clock_host must be a string")
    host = host.strip()
    if host:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _HOSTNAME.fullmatch(host):
                raise ValueError("android_clock_host must be a valid IP address or hostname")
    result = {"android_clock_host": host}
    for key in ("capture_rtp", "preview_windows"):
        if not isinstance(values[key], bool):
            raise ValueError(f"{key} must be boolean")
        result[key] = values[key]
    return result


def render(values: dict[str, Any]) -> str:
    values = validate(values)
    host = values["android_clock_host"].replace('"', '\\"')
    return (
        "# Generated locally by DJI Transport Edge dashboard. Ignored by Git.\n"
        "/dji_edge_driver:\n  ros__parameters:\n"
        f"    android_clock_host: \"{host}\"\n"
        f"    capture_rtp: {'true' if values['capture_rtp'] else 'false'}\n"
        f"    preview_windows: {'true' if values['preview_windows'] else 'false'}\n"
    )


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
