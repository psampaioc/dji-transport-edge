"""One-shot UDP clock exchanges initiated by the Edge."""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ClockExchange:
    response_data: bytes
    remote: tuple[str, int]
    t0_edge_send_mono_ns: int
    t3_edge_receive_mono_ns: int


class ClockPinger:
    """Use one ephemeral source socket and ignore delayed, unmatched replies."""

    def __init__(self, timeout_s: float = 0.5, max_bytes: int = 1200) -> None:
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def close(self) -> None:
        self._socket.close()

    def exchange_once(
        self,
        target: tuple[str, int],
        session: str,
        sequence: int,
        matches: Callable[[ClockExchange], bool] | None = None,
    ) -> ClockExchange:
        t0_edge_send_mono_ns = time.monotonic_ns()
        ping = {
            "v": 1,
            "type": "clock_ping",
            "session": session,
            "stream": "clock",
            "seq": sequence,
            "t0_edge_send_mono_ns": t0_edge_send_mono_ns,
        }
        self._socket.sendto(json.dumps(ping, separators=(",", ":")).encode(), target)
        deadline = time.monotonic() + self.timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("timed out")
            self._socket.settimeout(remaining)
            data, remote = self._socket.recvfrom(self.max_bytes + 1)
            exchange = ClockExchange(data, remote, t0_edge_send_mono_ns, time.monotonic_ns())
            if matches is None or matches(exchange):
                return exchange
