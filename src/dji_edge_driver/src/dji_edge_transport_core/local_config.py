"""Validated, atomic local dashboard configuration; never a generic editor."""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import socket
import tempfile
from typing import Any, Callable


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
            address = ipaddress.ip_address(host)
        except ValueError:
            if not _HOSTNAME.fullmatch(host):
                raise ValueError("android_clock_host must be a valid IPv4 address or hostname")
        else:
            if address.version != 4:
                raise ValueError("android_clock_host must be an IPv4 address or hostname")
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


def resolve_ipv4_udp_target(host: str, port: int) -> tuple[str, int]:
    """Resolve a configured clock target through the IPv4 UDP contract only."""
    results = socket.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
    if not results:
        raise OSError(f"no IPv4 UDP address found for {host}")
    address = results[0][4]
    return address[0], address[1]


def save_requested_config(
    values: dict[str, Any],
    config_path: str | Path,
    restart_request_path: str | Path,
    *,
    restart: bool,
    request_stop: Callable[[], None],
) -> dict[str, Any]:
    """Persist the allowed config and request one managed restart when asked.

    The stop callback is deliberately last: a marker failure leaves the current
    process alive instead of shutting it down without a supervisor relaunch.
    """
    target = atomic_write(config_path, values)
    result = {"status": "saved", "source": str(target), "restarting": False}
    if not restart:
        return result

    marker = Path(restart_request_path)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("dashboard restart requested\n", encoding="utf-8")
    except OSError as error:
        raise OSError(f"configuration saved but restart was not requested: {error}") from error
    request_stop()
    return {**result, "status": "saved; restarting managed stack", "restarting": True}
