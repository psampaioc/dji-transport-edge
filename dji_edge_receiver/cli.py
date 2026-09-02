from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import sys
from threading import Event
from importlib import resources

from .config import load_config
from .dashboard import DashboardApplication
from .protocol import ProtocolError, decode_json_packet
from .server import EdgeReceiver
from .video import gstreamer_command


def user_config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "dji-edge-receiver" / "config.toml"


def resolve_config_path(value: str | None, *, cwd: Path | None = None) -> Path:
    """Resolve an explicit, environment, local, or per-user configuration."""
    if value:
        return Path(value).expanduser().resolve()
    from_environment = os.environ.get("DJI_EDGE_RECEIVER_CONFIG")
    if from_environment:
        return Path(from_environment).expanduser().resolve()
    local = (cwd or Path.cwd()) / "config.toml"
    if local.is_file():
        return local.resolve()
    persistent = user_config_path()
    if persistent.is_file():
        return persistent
    raise FileNotFoundError(
        "no configuration found; run 'dji-edge init-config --from /path/to/config.toml' "
        "once, or pass --config /path/to/config.toml"
    )


def _add_config_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="TOML path; defaults to cwd/config.toml then ~/.config/dji-edge-receiver/config.toml")


def rebase_evidence_directory(contents: str, source: Path) -> str:
    """Keep evidence in the same location when a TOML is copied to user config."""
    original = load_config(source).storage.evidence_dir.resolve()
    storage = re.search(r"(?ms)^\[storage\]\s*$.*?(?=^\[|^\[\[|\Z)", contents)
    if storage is None:
        return contents
    section = storage.group(0)
    replacement = f'evidence_dir = "{original}"'
    if re.search(r"(?m)^\s*evidence_dir\s*=.*$", section):
        section = re.sub(r"(?m)^\s*evidence_dir\s*=.*$", replacement, section, count=1)
    else:
        section = section.rstrip() + "\n" + replacement + "\n"
    return contents[:storage.start()] + section + contents[storage.end():]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dji-edge-receiver")
    subcommands = parser.add_subparsers(dest="command")
    run = subcommands.add_parser("run", help="run telemetry, metadata, clock, RTP, and HTTP receivers")
    _add_config_option(run)
    dashboard = subcommands.add_parser(
        "dashboard", help="run the local measurement dashboard and native video pipelines"
    )
    _add_config_option(dashboard)
    dashboard.add_argument("--no-browser", action="store_true", help="print the local URL without opening a browser")
    pipeline = subcommands.add_parser("print-gstreamer", help="print generated GStreamer commands")
    _add_config_option(pipeline)
    validate = subcommands.add_parser("validate-packet", help="validate one JSON datagram from a file")
    validate.add_argument("path", type=Path)
    validate.add_argument("--version", type=int, default=1)
    validate.add_argument("--max-bytes", type=int, default=1200)
    init = subcommands.add_parser("init-config", help="create the per-user configuration used from any directory")
    init.add_argument("--from", dest="source", type=Path, help="copy an existing, working TOML configuration")
    init.add_argument("--path", type=Path, default=user_config_path(), help="destination TOML path")
    return parser


def main(argv: list[str] | None = None) -> int:
    supplied = list(sys.argv[1:] if argv is None else argv)
    commands = {"run", "dashboard", "print-gstreamer", "validate-packet", "init-config"}
    if not supplied or supplied[0] not in commands:
        supplied.insert(0, "dashboard")
    parser = _parser()
    args = parser.parse_args(supplied)
    # The short global command is the operator application. Advanced modes
    # remain explicit subcommands for systemd and diagnostics.
    if args.command == "init-config":
        destination = args.path.expanduser()
        if destination.exists():
            print(f"refusing to overwrite existing config: {destination}")
            return 2
        if args.source is not None:
            source = args.source.expanduser().resolve()
            # Validate before copying a configuration that will become the default.
            load_config(source)
            contents = rebase_evidence_directory(source.read_text(encoding="utf-8"), source)
        else:
            contents = resources.files("dji_edge_receiver").joinpath("config.example.toml").read_text(encoding="utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents, encoding="utf-8")
        print(f"created edge receiver config: {destination}")
        return 0
    if args.command == "print-gstreamer":
        config = load_config(resolve_config_path(args.config))
        for stream in config.video_streams:
            print(f"{stream.name}: {' '.join(gstreamer_command(stream))}")
        return 0
    if args.command == "validate-packet":
        try:
            packet = decode_json_packet(args.path.read_bytes(), args.version, args.max_bytes)
        except ProtocolError as exc:
            print(json.dumps({"valid": False, "error": str(exc)}))
            return 2
        print(json.dumps({"valid": True, "type": packet.packet_type, "session": packet.session, "sequence": packet.sequence}))
        return 0

    if args.command == "dashboard":
        stopped = Event()
        application = DashboardApplication(resolve_config_path(args.config), shutdown_request=stopped.set)
        signal.signal(signal.SIGINT, lambda signum, frame: stopped.set())
        signal.signal(signal.SIGTERM, lambda signum, frame: stopped.set())
        application.start(open_browser=not args.no_browser)
        print(f"DJI edge dashboard running; dashboard={application.url}")
        try:
            stopped.wait()
        finally:
            application.stop()
        return 0

    config = load_config(resolve_config_path(args.config))
    receiver = EdgeReceiver(config)
    stopped = Event()
    signal.signal(signal.SIGINT, lambda signum, frame: stopped.set())
    signal.signal(signal.SIGTERM, lambda signum, frame: stopped.set())
    receiver.start()
    print(
        f"DJI edge receiver running; state=http://{config.network.http_host}:"
        f"{receiver.http.port}/v1/state evidence={config.storage.evidence_dir}"
    )
    try:
        stopped.wait()
    finally:
        receiver.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
