#!/usr/bin/env python3
"""Send golden packets to a running edge receiver and verify its state API."""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "deploy/protocol/v1/golden_packets.json"


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} from {url}")
        value = json.load(response)
    if not isinstance(value, dict):
        raise RuntimeError(f"non-object response from {url}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--telemetry-port", type=int, default=5500)
    parser.add_argument("--frame-port", type=int, default=5501)
    parser.add_argument("--video-port", type=int, default=5600)
    parser.add_argument("--http-port", type=int, default=8088)
    args = parser.parse_args()

    try:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        health = get_json(f"http://{args.host}:{args.http_port}/health")
    except (OSError, json.JSONDecodeError, RuntimeError, urllib.error.URLError) as exc:
        print(f"error: receiver preflight failed: {exc}", file=sys.stderr)
        return 1
    if health.get("status") != "ok" or health.get("schema_version") != 1:
        print(f"error: unexpected health response: {health}", file=sys.stderr)
        return 1

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for packet in fixture["telemetry_json"]:
        destination = args.frame_port if packet["type"] == "video_au" else args.telemetry_port
        encoded = json.dumps(packet, separators=(",", ":"), sort_keys=True).encode()
        udp.sendto(encoded, (args.host, destination))
    for packet in fixture["rtp_h264"]:
        udp.sendto(base64.b64decode(packet["base64"], validate=True), (args.host, args.video_port))
    udp.close()

    deadline = time.monotonic() + 3
    state: dict = {}
    while time.monotonic() < deadline:
        try:
            state = get_json(f"http://{args.host}:{args.http_port}/v1/state")
        except (RuntimeError, urllib.error.URLError):
            time.sleep(0.05)
            continue
        transport = state.get("transport", {})
        if transport.get("accepted_packets", 0) >= 5 and transport.get("rtp_packets", 0) >= 3:
            break
        time.sleep(0.05)

    failures: list[str] = []
    if state.get("session") != "fixture-session-0001":
        failures.append("fixture session was not observed")
    if state.get("flight") is None or state.get("rtk") is None or not state.get("gimbals"):
        failures.append("flight/RTK/gimbal views are incomplete")
    if "primary" not in state.get("video_frames", {}):
        failures.append("primary frame metadata was not observed")
    transport = state.get("transport", {})
    if transport.get("accepted_packets") != 5:
        failures.append(f"expected 5 accepted JSON packets, got {transport.get('accepted_packets')}")
    if transport.get("rejected_packets") != 0:
        failures.append(f"receiver rejected {transport.get('rejected_packets')} JSON packets")
    if transport.get("rtp_packets") != 3 or transport.get("rtp_rejected") != 0:
        failures.append(
            f"expected 3 accepted/0 rejected RTP packets, got "
            f"{transport.get('rtp_packets')}/{transport.get('rtp_rejected')}"
        )
    if failures:
        for failure in failures:
            print(f"error: {failure}", file=sys.stderr)
        return 1
    print("Edge receiver golden-data verification passed: 5 JSON packets, 3 RTP/H.264 packets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
