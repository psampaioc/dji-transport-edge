from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class NetworkConfig:
    bind_host: str = "0.0.0.0"
    telemetry_port: int = 5500
    frame_metadata_port: int = 5501
    clock_port: int = 5502
    http_host: str = "127.0.0.1"
    http_port: int = 8088
    max_datagram_bytes: int = 1200
    android_clock_host: str | None = None
    android_clock_port: int = 5502
    clock_ping_interval_s: float = 1.0


@dataclass(frozen=True)
class StorageConfig:
    evidence_dir: Path = Path("./evidence")
    capture_rtp: bool = True
    fsync: bool = False


@dataclass(frozen=True)
class DashboardConfig:
    """Desktop-only local dashboard settings.

    The dashboard deliberately has no network-facing mode.  Remote access is
    a separate future decision, not a configuration accident.
    """

    host: str = "127.0.0.1"
    port: int = 8090
    open_browser: bool = True


@dataclass(frozen=True)
class VideoStreamConfig:
    name: str = "primary"
    input_port: int = 5600
    pipeline_port: int = 5602
    rtp_payload_type: int = 96
    latency_ms: int = 20
    gstreamer_enabled: bool = True
    # Pre-ROS validation decodes and drops frames. The ROS adapter will replace
    # this tail with an in-process appsink; shmsink is intentionally avoided.
    sink: str = (
        "avdec_h264 max-threads=2 ! videoconvert ! video/x-raw,format=BGR ! "
        "queue leaky=downstream max-size-buffers=1 ! "
        "fakesink sync=false"
    )


@dataclass(frozen=True)
class ReceiverConfig:
    protocol_version: int = 1
    network: NetworkConfig = field(default_factory=NetworkConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    video_streams: tuple[VideoStreamConfig, ...] = (VideoStreamConfig(),)


def _known(cls, values: dict) -> dict:
    names = cls.__dataclass_fields__.keys()
    unknown = set(values) - set(names)
    if unknown:
        raise ValueError(f"unknown {cls.__name__} settings: {sorted(unknown)}")
    return values


def _port(value: int, name: str, allow_zero: bool = False) -> None:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 65535:
        raise ValueError(f"{name} must be an integer between {minimum} and 65535")


def validate_config(config: ReceiverConfig) -> None:
    if isinstance(config.protocol_version, bool) or not isinstance(config.protocol_version, int) \
            or config.protocol_version < 1:
        raise ValueError("protocol_version must be an integer >= 1")
    network = config.network
    for name in ("bind_host", "http_host"):
        if not getattr(network, name):
            raise ValueError(f"network.{name} must not be empty")
    _port(network.telemetry_port, "network.telemetry_port")
    _port(network.frame_metadata_port, "network.frame_metadata_port")
    _port(network.clock_port, "network.clock_port")
    _port(network.http_port, "network.http_port", allow_zero=True)
    _port(network.android_clock_port, "network.android_clock_port")
    if not 256 <= network.max_datagram_bytes <= 65507:
        raise ValueError("network.max_datagram_bytes must be between 256 and 65507")
    if network.clock_ping_interval_s <= 0:
        raise ValueError("network.clock_ping_interval_s must be positive")

    dashboard = config.dashboard
    if dashboard.host != "127.0.0.1":
        raise ValueError("dashboard.host must be exactly 127.0.0.1")
    _port(dashboard.port, "dashboard.port", allow_zero=True)

    names = [stream.name for stream in config.video_streams]
    if len(names) != len(set(names)):
        raise ValueError("video stream names must be unique")
    if any(name not in {"primary", "secondary"} for name in names):
        raise ValueError("video stream names must be 'primary' or 'secondary'")
    local_udp_ports = [network.telemetry_port, network.frame_metadata_port, network.clock_port]
    for index, stream in enumerate(config.video_streams):
        prefix = f"video_streams[{index}]"
        _port(stream.input_port, f"{prefix}.input_port")
        _port(stream.pipeline_port, f"{prefix}.pipeline_port")
        if not 0 <= stream.rtp_payload_type <= 127:
            raise ValueError(f"{prefix}.rtp_payload_type must be between 0 and 127")
        if stream.latency_ms < 0:
            raise ValueError(f"{prefix}.latency_ms must be >= 0")
        if not stream.sink.strip():
            raise ValueError(f"{prefix}.sink must not be empty")
        local_udp_ports.extend((stream.input_port, stream.pipeline_port))
    if len(local_udp_ports) != len(set(local_udp_ports)):
        raise ValueError("local UDP input and pipeline ports must be unique")


def load_config(path: str | Path) -> ReceiverConfig:
    config_path = Path(path)
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)

    allowed = {"protocol_version", "network", "storage", "dashboard", "video_streams"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown top-level settings: {sorted(unknown)}")

    network = NetworkConfig(**_known(NetworkConfig, raw.get("network", {})))
    storage_values = dict(raw.get("storage", {}))
    if "evidence_dir" in storage_values:
        evidence = Path(storage_values["evidence_dir"])
        if not evidence.is_absolute():
            evidence = (config_path.parent / evidence).resolve()
        storage_values["evidence_dir"] = evidence
    storage = StorageConfig(**_known(StorageConfig, storage_values))
    dashboard = DashboardConfig(**_known(DashboardConfig, raw.get("dashboard", {})))

    stream_values = raw.get("video_streams")
    if stream_values is None:
        streams = (VideoStreamConfig(),)
    else:
        if not isinstance(stream_values, list) or not stream_values:
            raise ValueError("video_streams must contain at least one [[video_streams]] table")
        streams = tuple(VideoStreamConfig(**_known(VideoStreamConfig, item)) for item in stream_values)

    config = ReceiverConfig(
        protocol_version=int(raw.get("protocol_version", 1)),
        network=network,
        storage=storage,
        dashboard=dashboard,
        video_streams=streams,
    )
    validate_config(config)
    return config
