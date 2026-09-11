"""One bounded GStreamer feed with observable failure and recovery state."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable

from .protocol import ProtocolError, parse_rtp_packet
from .rtp import RawRtpCapture, RtpMetrics
from .video import LatestFrameBuffer, RtpPtsBinding


@dataclass(frozen=True)
class DecoderChoice:
    """The decoder actually selected for one feed, with an operator-facing reason."""

    element: str
    backend: str
    reason: str


def select_decoder(factory_lookup: Callable[[str], object | None]) -> DecoderChoice:
    """Prefer NVIDIA only when the running GStreamer registry exposes it."""
    try:
        nvidia_factory = factory_lookup("nvh264dec")
    except Exception as error:
        return DecoderChoice("avdec_h264", "cpu", f"NVIDIA probe failed: {error}")
    if nvidia_factory is None:
        return DecoderChoice("avdec_h264", "cpu", "GStreamer factory nvh264dec is unavailable")
    return DecoderChoice("nvh264dec", "nvidia", "GStreamer factory nvh264dec is available")


@dataclass(frozen=True)
class DecodedFrame:
    width: int
    height: int
    data: bytes
    decoded_mono_ns: int
    rtp_identity: tuple[int, int] | None


@dataclass
class FeedState:
    """Small, deterministic state machine used by the feed watchdog."""

    name: str
    decoded_frames: int = 0
    dropped_old_frames: int = 0
    last_decoded_mono_ns: int | None = None
    width: int = 0
    height: int = 0
    last_bus_message: str | None = None
    pipeline_error: str | None = None
    preview_renderer_suspect: bool = False
    restart_requested: bool = False
    restart_count: int = 0
    restart_attempts: int = 0
    restart_exhausted: bool = False
    last_failure: str | None = None
    last_failure_mono_ns: int | None = None
    last_failure_preview_renderer_suspect: bool = False

    def mark_decoded(self, now_ns: int, width: int, height: int) -> None:
        self.decoded_frames += 1
        self.last_decoded_mono_ns = now_ns
        self.width, self.height = width, height

    def mark_old_frame_dropped(self) -> None:
        self.dropped_old_frames += 1

    def mark_failure(self, message: str) -> None:
        self.last_bus_message = message
        self.pipeline_error = message
        lowered = message.lower()
        self.preview_renderer_suspect = any(
            token in lowered for token in ("ximagesink", "x11", "wayland", "display")
        )
        self.last_failure = message
        self.last_failure_mono_ns = time.monotonic_ns()
        self.last_failure_preview_renderer_suspect = self.preview_renderer_suspect
        self.restart_requested = True

    def mark_watchdog(self) -> None:
        self.mark_failure("WATCHDOG: RTP advancing while decoded frames are stalled")

    def is_stalled(self, rtp_age_s: float | None, now_ns: int, threshold_s: float) -> bool:
        if rtp_age_s is None or rtp_age_s > 0.75 or self.last_decoded_mono_ns is None:
            return False
        return (now_ns - self.last_decoded_mono_ns) / 1_000_000_000 > threshold_s


class FeedPipeline:
    """Own the per-feed GStreamer lifecycle and the bounded decoded handoff.

    The class deliberately does not know about ROS publishers or navigation.
    It exposes the newest decoded frame and feed health to the small ROS-facing
    coordinator. GStreamer is still decoded exactly once per feed.
    """

    _STALL_AFTER_S = 2.0
    _RESTART_BACKOFF_S = (0.5, 1.0, 2.0, 5.0)

    def __init__(
        self,
        *,
        name: str,
        port: int,
        bind_host: str,
        payload_type: int,
        latency_ms: int,
        preview_windows: bool,
        capture_path: str | None,
        logger: Callable[[str], None],
    ) -> None:
        self.name = name
        self.port = port
        self.bind_host = bind_host
        self.payload_type = payload_type
        self.latency_ms = latency_ms
        self.preview_windows = preview_windows
        self._logger = logger

        self.metrics = RtpMetrics(payload_type)
        self.capture = RawRtpCapture(capture_path)
        self._latest_frame: LatestFrameBuffer[DecodedFrame] = LatestFrameBuffer()
        self._pts_binding = RtpPtsBinding()
        self._ros_consumer_active = False
        self._stopping = threading.Event()
        self._lock = threading.RLock()
        self._bus_stop = threading.Event()
        self._bus_thread: threading.Thread | None = None
        self._sample_handler_id = None
        self._sink = None
        self._pipeline = None
        self._gst = None
        self.decoder = DecoderChoice("avdec_h264", "cpu", "GStreamer has not started")

        self.state = FeedState(name)
        self._next_restart_mono = 0.0
        self._intentional_close = False

        self._start_pipeline()

    @property
    def pipeline(self):
        """Compatibility/debug access; lifecycle remains owned here."""
        return self._pipeline

    @property
    def gst(self):
        return self._gst

    @property
    def decoded_frames(self) -> int:
        return self.state.decoded_frames

    @property
    def dropped_old_frames(self) -> int:
        return self.state.dropped_old_frames

    @property
    def width(self) -> int:
        return self.state.width

    @property
    def height(self) -> int:
        return self.state.height

    @property
    def error(self) -> str | None:
        return self.state.pipeline_error

    def _description(self, decoder: str | None = None) -> str:
        selected_decoder = self.decoder.element if decoder is None else decoder
        source_to_bgr = (
            f"udpsrc name={self.name}_source address={self.bind_host} "
            f"port={self.port} buffer-size=4194304 "
            "caps=application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,"
            f"payload={self.payload_type} ! rtpjitterbuffer name={self.name}_jitter "
            f"mode=none latency={self.latency_ms} "
            "drop-on-latency=true do-lost=true ! rtph264depay "
            "wait-for-keyframe=true request-keyframe=true ! "
            f"h264parse config-interval=-1 ! {selected_decoder} ! videoconvert ! "
            "video/x-raw,format=BGR"
        )
        appsink = (
            f" ! queue leaky=downstream max-size-buffers=1 ! video/x-raw,format=BGR ! "
            f"appsink name={self.name}_sink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        if not self.preview_windows:
            return source_to_bgr + appsink
        return (
            source_to_bgr
            + f" ! tee name={self.name}_decoded "
            f"{self.name}_decoded. ! queue leaky=downstream max-size-buffers=1 ! "
            f"video/x-raw,format=BGR ! appsink name={self.name}_sink emit-signals=true "
            f"max-buffers=1 drop=true sync=false {self.name}_decoded. ! "
            "queue leaky=downstream max-size-buffers=1 ! videoconvert ! ximagesink sync=false"
        )

    def _start_pipeline(self) -> None:
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            self._gst = Gst
            Gst.init(None)
            preferred = select_decoder(Gst.ElementFactory.find)
            self._start_with_decoder(preferred)
        except Exception as error:
            self.state.pipeline_error = str(error)
            self.state.last_bus_message = f"startup error: {error}"
            self.state.last_failure = self.state.last_bus_message
            self.state.last_failure_mono_ns = time.monotonic_ns()
            self._logger(f"{self.name} video unavailable: {error}")

    def _start_with_decoder(self, choice: DecoderChoice) -> None:
        """Start the selected full graph; use CPU only when NVIDIA cannot start."""
        self.decoder = choice
        try:
            self._install_pipeline(self._gst.parse_launch(self._description(choice.element)))
            if choice.backend == "nvidia":
                self.decoder = DecoderChoice(
                    choice.element, choice.backend, "NVIDIA decoder pipeline started"
                )
            return
        except Exception as error:
            self._teardown_pipeline()
            if choice.backend != "nvidia":
                raise
            fallback = DecoderChoice(
                "avdec_h264", "cpu", f"NVIDIA decoder startup failed: {error}"
            )
            self.decoder = fallback
            self._install_pipeline(self._gst.parse_launch(self._description(fallback.element)))

    def _install_pipeline(self, pipeline) -> None:
        Gst = self._gst
        with self._lock:
            self._pipeline = pipeline
            self._sink = pipeline.get_by_name(f"{self.name}_sink")
            self._sample_handler_id = self._sink.connect("new-sample", self._on_sample)
            source = pipeline.get_by_name(f"{self.name}_source")
            source.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._on_rtp)
            jitter = pipeline.get_by_name(f"{self.name}_jitter")
            jitter.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._on_jitter_rtp)
            result = pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("GStreamer pipeline could not enter PLAYING")
            self.state.pipeline_error = None
            self.state.last_bus_message = None
            self.state.preview_renderer_suspect = False
            self._bus_stop.clear()
            self._bus_thread = threading.Thread(
                target=self._bus_loop, name=f"{self.name}-gst-bus", daemon=True
            )
            self._bus_thread.start()

    def _bus_loop(self) -> None:
        Gst = self._gst
        while not self._bus_stop.is_set() and not self._stopping.is_set():
            pipeline = self._pipeline
            if pipeline is None:
                return
            message = pipeline.get_bus().timed_pop_filtered(
                100 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS,
            )
            if message is None:
                continue
            if self._stopping.is_set() or self._intentional_close:
                return
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                detail = str(error)
                if debug:
                    detail = f"{detail}; {debug}"
                self.record_bus_event("ERROR", detail)
            elif message.type == Gst.MessageType.EOS:
                self.record_bus_event("EOS", "pipeline ended")

    def record_bus_event(self, event: str, detail: str) -> bool:
        """Record a bus failure unless the feed is already closing."""
        if self._stopping.is_set() or self._intentional_close:
            return False
        self._mark_pipeline_failure(f"{event}: {detail}")
        return True

    def _mark_pipeline_failure(self, message: str) -> None:
        with self._lock:
            self.state.mark_failure(message)
            self._next_restart_mono = min(self._next_restart_mono, time.monotonic())
        self._logger(f"{self.name} GStreamer {message}")

    def _on_sample(self, sink):
        Gst = self._gst
        sample = sink.emit("pull-sample")
        if sample is None or self._stopping.is_set():
            return Gst.FlowReturn.OK
        caps = sample.get_caps().get_structure(0)
        width, height = caps.get_value("width"), caps.get_value("height")
        now = time.monotonic_ns()
        with self._lock:
            self.state.mark_decoded(now, width, height)
        if not self._ros_consumer_active:
            return Gst.FlowReturn.OK
        buffer = sample.get_buffer()
        mapped_ok, mapped = buffer.map(Gst.MapFlags.READ)
        if mapped_ok:
            try:
                pts_ns = buffer.pts
                if pts_ns == Gst.CLOCK_TIME_NONE:
                    pts_ns = None
                frame = DecodedFrame(
                    width, height, bytes(mapped.data), now, self._pts_binding.resolve(pts_ns),
                )
                if self._latest_frame.put(frame):
                    with self._lock:
                        self.state.mark_old_frame_dropped()
            finally:
                buffer.unmap(mapped)
        return Gst.FlowReturn.OK

    def _on_rtp(self, _pad, info):
        buffer = info.get_buffer()
        if buffer is not None:
            mapped_ok, mapped = buffer.map(self._gst.MapFlags.READ)
            if mapped_ok:
                try:
                    packet = self.metrics.observe(memoryview(mapped.data))
                finally:
                    buffer.unmap(mapped)
                if packet is not None and self.capture.enabled:
                    self.capture.write(buffer.extract_dup(0, buffer.get_size()))
        return self._gst.PadProbeReturn.OK

    def _on_jitter_rtp(self, _pad, info):
        buffer = info.get_buffer()
        if buffer is None:
            return self._gst.PadProbeReturn.OK
        try:
            mapped_ok, mapped = buffer.map(self._gst.MapFlags.READ)
            if not mapped_ok:
                return self._gst.PadProbeReturn.OK
            try:
                packet = parse_rtp_packet(memoryview(mapped.data), self.payload_type)
            finally:
                buffer.unmap(mapped)
            pts_ns = None if buffer.pts == self._gst.CLOCK_TIME_NONE else int(buffer.pts)
            self._pts_binding.observe(pts_ns, packet.ssrc, packet.timestamp)
        except ProtocolError:
            pass
        return self._gst.PadProbeReturn.OK

    def take_latest(self) -> DecodedFrame | None:
        return self._latest_frame.take()

    def set_ros_consumer_active(self, active: bool) -> None:
        self._ros_consumer_active = active
        if not active:
            self._latest_frame.clear()

    def _is_stalled(self) -> bool:
        rtp = self.metrics.snapshot()
        if rtp["last_datagram_age_s"] is None or rtp["last_datagram_age_s"] > 0.75:
            return False
        return self.state.is_stalled(
            rtp["last_datagram_age_s"], time.monotonic_ns(), self._STALL_AFTER_S
        )

    def maintain(self) -> None:
        """Run from the ROS timer; never rebuild a pipeline from a callback."""
        with self._lock:
            if self._stopping.is_set() or self.state.restart_exhausted:
                return
            if self._is_stalled():
                self.state.mark_watchdog()
            if not self.state.restart_requested or time.monotonic() < self._next_restart_mono:
                return
            self.state.restart_requested = False
        self._restart()

    def _restart(self) -> None:
        with self._lock:
            if self._stopping.is_set() or self.state.restart_exhausted:
                return
        self._teardown_pipeline()
        with self._lock:
            if self._stopping.is_set() or self.state.restart_exhausted:
                return
            try:
                self._pts_binding = RtpPtsBinding()
                self._latest_frame.clear()
                self._start_with_decoder(self.decoder)
                self.state.restart_count += 1
                self.state.restart_attempts = 0
                self.state.pipeline_error = None
                self.state.last_bus_message = f"restarted feed (count={self.state.restart_count})"
            except Exception as error:
                self.state.restart_attempts += 1
                self.state.pipeline_error = str(error)
                self.state.last_bus_message = f"restart error: {error}"
                if self.state.restart_attempts >= len(self._RESTART_BACKOFF_S):
                    self.state.restart_exhausted = True
                    self._logger(f"{self.name} GStreamer restart exhausted: {error}")
                    return
                delay = self._RESTART_BACKOFF_S[self.state.restart_attempts - 1]
                self._next_restart_mono = time.monotonic() + delay
                self.state.restart_requested = True

    def _teardown_pipeline(self) -> None:
        with self._lock:
            self._bus_stop.set()
            bus_thread = self._bus_thread
            pipeline = self._pipeline
            sink = self._sink
            sample_handler_id = self._sample_handler_id
            self._bus_thread = None
            self._pipeline = None
            self._sink = None
            self._sample_handler_id = None
        if pipeline is not None:
            if sample_handler_id is not None and sink is not None:
                sink.disconnect(sample_handler_id)
            pipeline.set_state(self._gst.State.NULL)
        if bus_thread is not None and bus_thread is not threading.current_thread():
            bus_thread.join(timeout=1.0)

    def snapshot(self) -> dict:
        now_ns = time.monotonic_ns()
        rtp = self.metrics.snapshot(now_ns)
        decoded_age = None if self.state.last_decoded_mono_ns is None else max(
            0.0, (now_ns - self.state.last_decoded_mono_ns) / 1_000_000_000
        )
        with self._lock:
            return {
                "decoder": {
                    "element": self.decoder.element,
                    "backend": self.decoder.backend,
                    "reason": self.decoder.reason,
                },
                "decoded_frames": self.state.decoded_frames,
                "dropped_old_frames": self.state.dropped_old_frames,
                "resolution": {"width": self.state.width, "height": self.state.height},
                "last_decoded_age_s": decoded_age,
                "pipeline_error": self.state.pipeline_error,
                "preview_renderer_suspect": self.state.preview_renderer_suspect,
                "last_bus_message": self.state.last_bus_message,
                "restart_count": self.state.restart_count,
                "restart_attempts": self.state.restart_attempts,
                "restart_exhausted": self.state.restart_exhausted,
                "decode_stalled": self._is_stalled(),
                "last_failure": self.state.last_failure,
                "last_failure_age_s": None if self.state.last_failure_mono_ns is None else max(
                    0.0, (now_ns - self.state.last_failure_mono_ns) / 1_000_000_000
                ),
                "last_failure_preview_renderer_suspect": (
                    self.state.last_failure_preview_renderer_suspect
                ),
                "rtp": rtp,
            }

    def close(self) -> None:
        with self._lock:
            if self._stopping.is_set():
                return
            self._intentional_close = True
            self._stopping.set()
            self._latest_frame.clear()
        self._teardown_pipeline()
        self.capture.close()
