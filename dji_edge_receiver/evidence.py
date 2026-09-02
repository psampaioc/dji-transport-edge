from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
import struct
from queue import Full, Queue
from threading import Lock, Thread
import time
from typing import Any


def _safe_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")[:64]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{clean or 'session'}-{digest}"


class EvidenceWriter:
    """Append-only packet evidence with bounded, explicit binary framing."""

    def __init__(
        self,
        root: Path,
        capture_rtp: bool = True,
        fsync: bool = False,
        queue_capacity: int = 16_384,
    ) -> None:
        self.root = root
        self.capture_rtp = capture_rtp
        self.fsync = fsync
        self._lock = Lock()
        self._session_dirs: dict[str, Path] = {}
        self._queue: Queue = Queue(maxsize=queue_capacity)
        self._dropped = 0
        self._write_errors = 0
        self._last_error: str | None = None
        self.root.mkdir(parents=True, exist_ok=True)
        self._thread = Thread(target=self._run, name="evidence-writer", daemon=True)
        self._thread.start()

    @property
    def dropped_records(self) -> int:
        with self._lock:
            return self._dropped

    def health(self) -> dict:
        with self._lock:
            return {
                "queue_depth": self._queue.qsize(),
                "dropped_records": self._dropped,
                "write_errors": self._write_errors,
                "last_error": self._last_error,
                "writer_alive": self._thread.is_alive(),
            }

    def _enqueue(self, operation: str, *values) -> None:
        try:
            self._queue.put_nowait((operation, values))
        except Full:
            with self._lock:
                self._dropped += 1

    def _run(self) -> None:
        while True:
            operation, values = self._queue.get()
            try:
                if operation == "stop":
                    return
                try:
                    getattr(self, f"_write_{operation}")(*values)
                except Exception as exc:  # keep ingest alive and expose degradation
                    with self._lock:
                        self._write_errors += 1
                        self._last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._queue.task_done()

    def _session_dir(self, session: str) -> Path:
        directory = self._session_dirs.get(session)
        if directory is None:
            directory = self.root / _safe_name(session)
            directory.mkdir(parents=True, exist_ok=True)
            manifest = directory / "manifest.json"
            if not manifest.exists():
                manifest.write_text(
                    json.dumps(
                        {
                            "evidence_schema_version": 1,
                            "session": session,
                            "edge_created_utc_ns": time.time_ns(),
                            "binary_rtp_record_format": "u64 edge_mono_ns + u32 packet_length + packet bytes (network byte order)",
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            self._session_dirs[session] = directory
        return directory

    def _append_json(self, path: Path, record: dict[str, Any]) -> None:
        encoded = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            if self.fsync:
                import os

                os.fsync(stream.fileno())

    def packet(
        self,
        session: str,
        category: str,
        raw: bytes,
        parsed: dict,
        edge_receive_ns: int,
        remote: tuple[str, int],
        validation: dict,
    ) -> None:
        record = {
            "edge_receive_mono_ns": edge_receive_ns,
            "edge_receive_utc_ns": time.time_ns(),
            "remote": f"{remote[0]}:{remote[1]}",
            "raw_utf8": raw.decode("utf-8", errors="replace"),
            "raw_base64": base64.b64encode(raw).decode("ascii"),
            "parsed": parsed,
            "validation": validation,
        }
        self._enqueue("packet", session, category, record)

    def _write_packet(self, session: str, category: str, record: dict) -> None:
        self._append_json(self._session_dir(session) / f"{category}.ndjson", record)

    def error(self, category: str, raw: bytes, edge_receive_ns: int, remote: tuple[str, int], error: str) -> None:
        record = {
            "edge_receive_mono_ns": edge_receive_ns,
            "edge_receive_utc_ns": time.time_ns(),
            "category": category,
            "remote": f"{remote[0]}:{remote[1]}",
            "error": error,
            "raw_base64": base64.b64encode(raw).decode("ascii"),
        }
        self._enqueue("error", record)

    def _write_error(self, record: dict) -> None:
        directory = self.root / "_receiver"
        directory.mkdir(parents=True, exist_ok=True)
        self._append_json(directory / "protocol_errors.ndjson", record)

    def clock(self, session: str, record: dict) -> None:
        self._enqueue("clock", session, record)

    def _write_clock(self, session: str, record: dict) -> None:
        self._append_json(self._session_dir(session) / "clock.ndjson", record)

    def rtp(self, session: str, stream: str, edge_receive_ns: int, raw: bytes) -> None:
        if not self.capture_rtp:
            return
        self._enqueue("rtp", session, stream, edge_receive_ns, raw)

    def _write_rtp(self, session: str, stream: str, edge_receive_ns: int, raw: bytes) -> None:
        framed = struct.pack("!QI", edge_receive_ns, len(raw)) + raw
        path = self._session_dir(session) / f"video-{_safe_name(stream)}.rtpbin"
        with path.open("ab") as output:
            output.write(framed)

    def close(self) -> None:
        self._queue.put(("stop", ()))
        self._thread.join(timeout=10)
