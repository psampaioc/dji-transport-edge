from __future__ import annotations

import shlex
import socket
import struct
import subprocess
from collections import deque
from threading import Event, Lock, Thread
import time
from typing import Callable

from .config import VideoStreamConfig
from .evidence import EvidenceWriter
from .h264 import H264RtpAnalyzer
from .protocol import ProtocolError, parse_rtp_packet
from .state import LatestState, SequenceTracker


def gstreamer_command(config: VideoStreamConfig) -> list[str]:
    pipeline = (
        f"udpsrc address=127.0.0.1 port={config.pipeline_port} buffer-size=2097152 "
        f"caps=application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload={config.rtp_payload_type} ! "
        f"rtpjitterbuffer latency={config.latency_ms} drop-on-latency=true do-lost=true ! "
        "rtph264depay wait-for-keyframe=true request-keyframe=true ! "
        "h264parse config-interval=-1 ! "
        f"{config.sink}"
    )
    return ["gst-launch-1.0", "-e", *shlex.split(pipeline)]


class RtpRelay:
    """Validate RTP and relay packets unchanged to a local GStreamer port."""

    def __init__(
        self,
        config: VideoStreamConfig,
        bind_host: str,
        evidence: EvidenceWriter,
        state: LatestState,
        session_for_ssrc: Callable[[int], str | None],
    ) -> None:
        self.config = config
        self.bind_host = bind_host
        self.evidence = evidence
        self.state = state
        self.session_for_ssrc = session_for_ssrc
        self._stop = Event()
        self._thread: Thread | None = None
        self._socket: socket.socket | None = None
        self._pipeline: subprocess.Popen | None = None
        self._sequence = SequenceTracker(modulus=1 << 16)
        self._last_packet_ns: int | None = None
        self._relay_errors = 0
        self._stats_lock = Lock()
        self._packets_received = 0
        self._bytes_received = 0
        self._packets_rejected = 0
        self._sequence_gaps = 0
        self._duplicates = 0
        self._out_of_order = 0
        self._access_units = 0
        self._access_unit_bytes = 0
        self._max_access_unit_bytes = 0
        self._current_access_unit_bytes = 0
        self._marker_timestamps: deque[int] = deque(maxlen=120)
        self._first_packet_ns: int | None = None
        self._analyzer = H264RtpAnalyzer()
        self._kernel_drop_count = 0
        self._kernel_drop_supported = False
        self._socket_receive_buffer_bytes: int | None = None

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self._socket_receive_buffer_bytes = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        overflow_option = getattr(socket, "SO_RXQ_OVFL", 40)
        try:
            sock.setsockopt(socket.SOL_SOCKET, overflow_option, 1)
            self._kernel_drop_supported = True
        except OSError:
            self._kernel_drop_supported = False
        sock.settimeout(0.2)
        sock.bind((self.bind_host, self.config.input_port))
        self._socket = sock
        if self.config.gstreamer_enabled:
            self._pipeline = subprocess.Popen(gstreamer_command(self.config))
            # gst-launch validates the element graph immediately. Surface a
            # missing plugin/property as startup failure instead of claiming
            # the receiver is live with no decoder behind it.
            time.sleep(0.2)
            if self._pipeline.poll() is not None:
                sock.close()
                raise RuntimeError(
                    f"GStreamer pipeline for {self.config.name!r} exited during startup "
                    f"with status {self._pipeline.returncode}"
                )
        self._thread = Thread(target=self._run, name=f"rtp-{self.config.name}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._socket is not None
        sock = self._socket
        forward = ("127.0.0.1", self.config.pipeline_port)
        while not self._stop.is_set():
            try:
                if self._kernel_drop_supported:
                    data, ancillary, _, remote = sock.recvmsg(65535, 64)
                    overflow_option = getattr(socket, "SO_RXQ_OVFL", 40)
                    for level, kind, value in ancillary:
                        if level == socket.SOL_SOCKET and kind == overflow_option and len(value) >= 4:
                            self._kernel_drop_count = max(
                                self._kernel_drop_count, struct.unpack("=I", value[:4])[0]
                            )
                else:
                    data, remote = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            now = time.monotonic_ns()
            self._last_packet_ns = now
            try:
                packet = parse_rtp_packet(data, self.config.rtp_payload_type)
            except ProtocolError as exc:
                with self._stats_lock:
                    self._packets_rejected += 1
                self.state.update_rtp(0, rejected=True)
                self.evidence.error(f"rtp:{self.config.name}", data, now, remote, str(exc))
                continue
            result = self._sequence.observe((packet.ssrc,), packet.sequence)
            payload = data[packet.header_size:packet.header_size + packet.payload_size]
            with self._stats_lock:
                if result.gap:
                    self._analyzer.discontinuity()
                self._analyzer.observe(payload, now)
                self._packets_received += 1
                self._bytes_received += len(data)
                self._sequence_gaps += result.gap
                if result.disposition == "duplicate":
                    self._duplicates += 1
                elif result.disposition == "out_of_order":
                    self._out_of_order += 1
                if self._first_packet_ns is None:
                    self._first_packet_ns = now
                self._current_access_unit_bytes += len(data)
                if packet.marker:
                    self._access_units += 1
                    self._access_unit_bytes += self._current_access_unit_bytes
                    self._max_access_unit_bytes = max(
                        self._max_access_unit_bytes, self._current_access_unit_bytes
                    )
                    self._current_access_unit_bytes = 0
                    self._marker_timestamps.append(packet.timestamp)
            self.state.update_rtp(
                len(data), disposition=result.disposition, gap=result.gap
            )
            session = self.session_for_ssrc(packet.ssrc) or "unknown-rtp-session"
            self.evidence.rtp(session, self.config.name, now, data)
            try:
                sock.sendto(data, forward)
            except OSError:
                self.state.update_rtp(0, rejected=True)
                self._relay_errors += 1

    def health(self) -> dict:
        pipeline_status = "disabled"
        pipeline_returncode = None
        if self._pipeline is not None:
            pipeline_returncode = self._pipeline.poll()
            pipeline_status = "running" if pipeline_returncode is None else "exited"
        with self._stats_lock:
            elapsed_ns = None
            if self._first_packet_ns is not None and self._last_packet_ns is not None:
                elapsed_ns = max(0, self._last_packet_ns - self._first_packet_ns)
            fps = None
            if len(self._marker_timestamps) >= 2:
                timestamp_span = (
                    self._marker_timestamps[-1] - self._marker_timestamps[0]
                ) & 0xFFFFFFFF
                if timestamp_span > 0:
                    fps = round((len(self._marker_timestamps) - 1) * 90000 / timestamp_span, 3)
            bitrate = None
            if elapsed_ns and elapsed_ns > 0:
                bitrate = round(self._bytes_received * 8 * 1_000_000_000 / elapsed_ns)
            access_unit_avg = None
            if self._access_units:
                access_unit_avg = round(self._access_unit_bytes / self._access_units)
            stats = {
                "packets_received": self._packets_received,
                "bytes_received": self._bytes_received,
                "packets_rejected": self._packets_rejected,
                "sequence_gaps": self._sequence_gaps,
                "duplicates": self._duplicates,
                "out_of_order": self._out_of_order,
                "access_units_observed": self._access_units,
                "access_unit_bytes_total": self._access_unit_bytes,
                "access_unit_bytes_avg": access_unit_avg,
                "access_unit_bytes_max": self._max_access_unit_bytes,
                "estimated_fps": fps,
                "estimated_bitrate_bps": bitrate,
                "last_rtp_timestamp": self._marker_timestamps[-1]
                if self._marker_timestamps else None,
                "kernel_socket_drops": self._kernel_drop_count,
                "kernel_drop_counter_supported": self._kernel_drop_supported,
                "socket_receive_buffer_bytes": self._socket_receive_buffer_bytes,
                "h264": self._analyzer.snapshot(),
                "decoded_frame_count_available": False,
                "measurement_boundary": "validated RTP access units; decoded frames require appsink",
            }
        return {
            "name": self.config.name,
            "input_port": self.config.input_port,
            "pipeline_port": self.config.pipeline_port,
            "pipeline_status": pipeline_status,
            "pipeline_returncode": pipeline_returncode,
            "relay_errors": self._relay_errors,
            "last_packet_mono_ns": self._last_packet_ns,
            "stats": stats,
        }

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            self._socket.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._pipeline is not None and self._pipeline.poll() is None:
            self._pipeline.terminate()
            try:
                self._pipeline.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._pipeline.kill()
