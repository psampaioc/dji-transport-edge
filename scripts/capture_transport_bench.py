#!/usr/bin/env python3
"""Capture a bounded, machine-readable props-off transport evidence report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from urllib.request import urlopen

from dji_edge_receiver.bench import build_report, summary, write_report


def get_state(url: str) -> dict:
    with urlopen(url, timeout=2) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError("edge state response is not an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-url", default="http://127.0.0.1:8088/v1/state")
    parser.add_argument("--duration-s", type=float, default=60.0)
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--out", type=Path, required=True,
                        help="new JSON evidence file; parent directory must already exist")
    args = parser.parse_args()
    if args.duration_s <= 0 or args.interval_s <= 0:
        parser.error("duration and interval must be positive")
    if args.out.exists():
        parser.error(f"refusing to overwrite existing evidence: {args.out}")
    if not args.out.parent.is_dir():
        parser.error(f"output parent does not exist: {args.out.parent}")

    deadline = time.monotonic() + args.duration_s
    samples: list[dict] = []
    while True:
        samples.append(get_state(args.state_url))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(args.interval_s, remaining))

    report = build_report(samples, state_url=args.state_url, requested_duration_s=args.duration_s)
    try:
        write_report(args.out, report)
    except (FileExistsError, FileNotFoundError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
