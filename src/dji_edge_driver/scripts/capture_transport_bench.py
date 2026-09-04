#!/usr/bin/env python3
"""Capture an attributable direct-edge dashboard bench without touching transport."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


def read_state(url: str) -> dict:
    with urlopen(f"{url.rstrip('/')}/v1/state", timeout=2) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-s", type=float, default=60.0)
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--url", default="http://127.0.0.1:8090")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.duration_s <= 0 or args.interval_s <= 0:
        parser.error("duration and interval must be positive")

    deadline = time.monotonic() + args.duration_s
    samples = []
    while True:
        captured_utc = datetime.now(timezone.utc).isoformat()
        try:
            state = read_state(args.url)
            samples.append({"captured_utc": captured_utc, "state": state, "error": None})
        except (OSError, ValueError) as error:
            samples.append({"captured_utc": captured_utc, "state": None, "error": str(error)})
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(args.interval_s, remaining))

    successful = [sample["state"] for sample in samples if sample["state"] is not None]
    report = {
        "schema_version": 1,
        "started_utc": samples[0]["captured_utc"],
        "duration_s": args.duration_s,
        "interval_s": args.interval_s,
        "sample_count": len(samples),
        "successful_samples": len(successful),
        "first_state": successful[0] if successful else None,
        "last_state": successful[-1] if successful else None,
        "samples": samples,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
