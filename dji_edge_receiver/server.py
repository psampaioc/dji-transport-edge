from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from threading import Event, Lock, Thread
import time
from typing import Callable

from .clock import ClockMapper
from .config import ReceiverConfig, validate_config
from .evidence import EvidenceWriter
from .protocol import CLOCK_TYPES, FRAME_TYPES, Packet, ProtocolError, decode_json_packet
from .state import LatestState, SequenceResult, SequenceTracker
from .video import RtpRelay


class PacketIngestor:
    def __init__(self, config: ReceiverConfig, evidence: EvidenceWriter, state: LatestState, clock: ClockMapper) -> None:
        self.config = config
        self.evidence = evidence
        self.state = state
        self.clock = clock
        self.sequence = SequenceTracker()
        self._timestamps: dict[tuple[str, str, str], int] = {}
        self._ssrc_sessions: dict[int, str] = {}
        self._video_streams = {stream.name for stream in config.video_streams}
        self._lock = Lock()

    def session_for_ssrc(self, ssrc: int) -> str | None:
        with self._lock:
            return self._ssrc_sessions.get(ssrc)

    def ingest(self, category: str, data: bytes, remote: tuple[str, int], edge_receive_ns: int) -> Packet | None:
        try:
            packet = decode_json_packet(
                data,
                expected_version=self.config.protocol_version,
                max_bytes=self.config.network.max_datagram_bytes,
            )
            if category == "frame_metadata" and packet.packet_type not in FRAME_TYPES:
                raise ProtocolError(f"{packet.packet_type} is not frame metadata")
            if category == "frame_metadata" and packet.stream not in self._video_streams:
                raise ProtocolError(f"unknown video stream {packet.stream!r}")
            if category == "telemetry" and packet.packet_type in FRAME_TYPES | CLOCK_TYPES:
                raise ProtocolError(f"{packet.packet_type} is not telemetry")
            if category == "clock" and packet.packet_type not in CLOCK_TYPES:
                raise ProtocolError(f"{packet.packet_type} is not a clock packet")
        except ProtocolError as exc:
            self.state.reject()
            self.evidence.error(category, data, edge_receive_ns, remote, str(exc))
            return None

        sequence_key = (packet.session, packet.packet_type, packet.stream)
        self.state.record_packet_size(packet.packet_type, len(data))
        sequence = self.sequence.observe(sequence_key, packet.sequence)
        timestamp_status = "first"
        with self._lock:
            prior_timestamp = self._timestamps.get(sequence_key)
            if sequence.is_newest and prior_timestamp is not None:
                timestamp_status = "ok" if packet.android_mono_ns >= prior_timestamp else "regression"
            if sequence.is_newest and timestamp_status != "regression":
                self._timestamps[sequence_key] = packet.android_mono_ns
            if packet.packet_type in FRAME_TYPES:
                self._ssrc_sessions[int(packet.raw["rtp_ssrc"])] = packet.session

        effective_sequence = sequence
        if timestamp_status == "regression":
            effective_sequence = SequenceResult("timestamp_regression")
            self.state.reject()

        validation = {
            "protocol": "ok",
            "sequence": sequence.disposition,
            "sequence_gap": sequence.gap,
            "timestamp": timestamp_status,
        }
        self.evidence.packet(
            packet.session,
            category,
            data,
            packet.raw,
            edge_receive_ns,
            remote,
            validation,
        )
        if packet.packet_type not in CLOCK_TYPES:
            self.state.update_packet(packet, edge_receive_ns, remote, effective_sequence)
        return packet


class UdpEndpoint:
    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        handler: Callable[[bytes, tuple[str, int], int, socket.socket], None],
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.handler = handler
        self._stop = Event()
        self._thread: Thread | None = None
        self._socket: socket.socket | None = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.bind((self.host, self.port))
        self._socket = sock
        self._thread = Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._socket is not None
        sock = self._socket
        while not self._stop.is_set():
            try:
                data, remote = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            self.handler(data, remote, time.monotonic_ns(), sock)

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=2)


class ClockService:
    def __init__(self, config: ReceiverConfig, ingestor: PacketIngestor, evidence: EvidenceWriter) -> None:
        self.config = config
        self.ingestor = ingestor
        self.evidence = evidence
        self._sequence = 0
        self._stop = Event()
        self._pinger: Thread | None = None
        self.endpoint = UdpEndpoint(
            "clock", config.network.bind_host, config.network.clock_port, self._handle
        )

    def start(self) -> None:
        self.endpoint.start()
        if self.config.network.android_clock_host:
            self._pinger = Thread(target=self._ping_loop, name="clock-pinger", daemon=True)
            self._pinger.start()

    def _handle(self, data: bytes, remote: tuple[str, int], receive_ns: int, sock: socket.socket) -> None:
        packet = self.ingestor.ingest("clock", data, remote, receive_ns)
        if packet is None:
            return
        if packet.packet_type == "clock_pong":
            try:
                sample = self.ingestor.clock.add_exchange(
                    int(packet.raw["t0_edge_send_mono_ns"]),
                    int(packet.raw["t1_android_rx_mono_ns"]),
                    int(packet.raw["t2_android_tx_mono_ns"]),
                    receive_ns,
                )
            except (KeyError, TypeError, ValueError) as exc:
                self.evidence.error("clock", data, receive_ns, remote, f"invalid clock pong: {exc}")
                self.ingestor.state.reject()
                return
            self.evidence.clock(packet.session, {**sample.as_dict(), "estimate": self.ingestor.clock.estimate()})
        elif packet.packet_type == "clock_ping":
            # Supports an edge-format peer probe as well as the normal
            # edge-initiated exchange. The peer can use the returned edge times.
            response = {
                "v": self.config.protocol_version,
                "type": "clock_pong",
                "session": packet.session,
                "stream": packet.stream,
                "seq": packet.sequence,
                "t0_edge_send_mono_ns": packet.raw["t0_edge_send_mono_ns"],
                "t1_android_rx_mono_ns": receive_ns,
                "t2_android_tx_mono_ns": time.monotonic_ns(),
            }
            sock.sendto(json.dumps(response, separators=(",", ":")).encode(), remote)

    def _ping_loop(self) -> None:
        destination = (
            self.config.network.android_clock_host,
            self.config.network.android_clock_port,
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while not self._stop.wait(self.config.network.clock_ping_interval_s):
            self._sequence += 1
            now = time.monotonic_ns()
            packet = {
                "v": self.config.protocol_version,
                "type": "clock_ping",
                "session": "edge-clock",
                "stream": "clock",
                "seq": self._sequence,
                "t0_edge_send_mono_ns": now,
            }
            sock.sendto(json.dumps(packet, separators=(",", ":")).encode(), destination)
        sock.close()

    def stop(self) -> None:
        self._stop.set()
        self.endpoint.stop()
        if self._pinger is not None:
            self._pinger.join(timeout=2)


class StateHttpServer:
    def __init__(self, host: str, port: int, state: LatestState,
                 health_provider: Callable[[], dict] | None = None) -> None:
        self.state = state
        self.health_provider = health_provider or (lambda: {"status": "ok"})
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in {"/health", "/v1/state"}:
                    self.send_error(404)
                    return
                health = outer.health_provider()
                if self.path == "/health":
                    body = {"schema_version": 1, **health}
                else:
                    body = outer.state.snapshot()
                    body["receiver_health"] = health
                encoded = json.dumps(body, separators=(",", ":")).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args) -> None:
                return

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.thread: Thread | None = None

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def start(self) -> None:
        self.thread = Thread(target=self.server.serve_forever, name="state-http", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)


class EdgeReceiver:
    def __init__(self, config: ReceiverConfig) -> None:
        validate_config(config)
        self.config = config
        self.clock = ClockMapper()
        self.evidence = EvidenceWriter(
            config.storage.evidence_dir,
            capture_rtp=config.storage.capture_rtp,
            fsync=config.storage.fsync,
        )
        self.state = LatestState(self.clock)
        self.ingestor = PacketIngestor(config, self.evidence, self.state, self.clock)
        self.telemetry = UdpEndpoint(
            "telemetry",
            config.network.bind_host,
            config.network.telemetry_port,
            lambda data, remote, now, sock: self.ingestor.ingest("telemetry", data, remote, now),
        )
        self.frame_metadata = UdpEndpoint(
            "frame-metadata",
            config.network.bind_host,
            config.network.frame_metadata_port,
            lambda data, remote, now, sock: self.ingestor.ingest("frame_metadata", data, remote, now),
        )
        self.clock_service = ClockService(config, self.ingestor, self.evidence)
        self.video = [
            RtpRelay(
                stream,
                config.network.bind_host,
                self.evidence,
                self.state,
                self.ingestor.session_for_ssrc,
            )
            for stream in config.video_streams
        ]
        self.http = StateHttpServer(
            config.network.http_host,
            config.network.http_port,
            self.state,
            self.runtime_health,
        )

    def runtime_health(self) -> dict:
        evidence = self.evidence.health()
        video = [relay.health() for relay in self.video]
        degraded = (
            evidence["dropped_records"] > 0
            or evidence["write_errors"] > 0
            or not evidence["writer_alive"]
            or any(item["pipeline_status"] == "exited" or item["relay_errors"] > 0
                   for item in video)
        )
        return {
            "status": "degraded" if degraded else "ok",
            "evidence": evidence,
            "video": video,
        }

    def start(self) -> None:
        cleanup: list[Callable[[], None]] = []
        try:
            # Start media first because missing GStreamer plugins are the most
            # likely deployment preflight failure.
            for relay in self.video:
                relay.start()
                cleanup.append(relay.stop)
            self.telemetry.start()
            cleanup.append(self.telemetry.stop)
            self.frame_metadata.start()
            cleanup.append(self.frame_metadata.stop)
            self.clock_service.start()
            cleanup.append(self.clock_service.stop)
            self.http.start()
            cleanup.append(self.http.stop)
        except Exception:
            for stop in reversed(cleanup):
                stop()
            self.evidence.close()
            raise

    def stop(self) -> None:
        for relay in reversed(self.video):
            relay.stop()
        self.http.stop()
        self.clock_service.stop()
        self.frame_metadata.stop()
        self.telemetry.stop()
        self.evidence.close()
