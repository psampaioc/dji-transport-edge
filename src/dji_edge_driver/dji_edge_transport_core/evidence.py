"""Bounded, append-only NDJSON evidence writing outside ingress threads."""

from __future__ import annotations

import json
from pathlib import Path
from queue import Full, Queue
from threading import Lock, Thread
from typing import Any
from uuid import uuid4


def create_session_directory(root: str | Path) -> Path:
    """Create one attributable evidence directory for a driver process."""
    import datetime

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(root) / f"edge-{stamp}-{uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


class EvidenceWriter:
    """Writes records asynchronously and drains accepted records on close."""

    def __init__(self, root: str | Path, max_records: int = 16_384) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._queue: Queue[tuple[str, dict[str, Any]] | None] = Queue(maxsize=max_records)
        self._lock = Lock()
        self._dropped = 0
        self._write_errors = 0
        self._closed = False
        self._worker = Thread(target=self._run, name="dji-edge-evidence", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                category, record = item
                path = self.root / f"{category}.ndjson"
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, separators=(",", ":"), sort_keys=True, allow_nan=False))
                    stream.write("\n")
            except (OSError, TypeError, ValueError):
                with self._lock:
                    self._write_errors += 1
            finally:
                self._queue.task_done()

    def write(self, category: str, record: dict[str, Any]) -> bool:
        if not category.replace("_", "").isalnum():
            raise ValueError("evidence category must be alphanumeric with optional underscores")
        with self._lock:
            if self._closed:
                return False
        try:
            self._queue.put_nowait((category, record))
            return True
        except Full:
            with self._lock:
                self._dropped += 1
            return False

    def health(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "queue_depth": self._queue.qsize(),
                "dropped_records": self._dropped,
                "write_errors": self._write_errors,
                "writer_alive": self._worker.is_alive(),
            }

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)
        self._worker.join(timeout=5)
