#!/usr/bin/env python3
"""Direct Android UDP/RTP ingestion; no HTTP polling or loopback relay."""

import json
import socket
import threading
import time

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from dji_edge_driver.msg import NavigationState
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image

from dji_edge_transport_core.clock import ClockMapper
from dji_edge_transport_core.dashboard import DashboardServer
from dji_edge_transport_core.evidence import EvidenceWriter, create_session_directory
from dji_edge_transport_core.navigation import build_navigation
from dji_edge_transport_core.protocol import CLOCK_TYPES, FRAME_TYPES, ProtocolError, decode_json_packet
from dji_edge_transport_core.rtp import RawRtpCapture, RtpMetrics
from dji_edge_transport_core.state import LatestState, SequenceTracker
from dji_edge_transport_core.video import LatestFrameBuffer


PARAMETERS = {
    "bind_host": "0.0.0.0", "telemetry_port": 5500, "frame_metadata_port": 5501,
    "clock_port": 5502, "primary_rtp_port": 5600, "fpv_rtp_port": 5610,
    "max_json_bytes": 1200, "rtp_payload_type": 96, "rtp_latency_ms": 20,
    "preview_windows": True, "publish_video": True, "capture_rtp": False,
    "evidence_dir": "evidence", "android_clock_host": "", "android_clock_port": 5502,
    "clock_ping_interval_s": 1.0, "dashboard_enabled": True,
    "dashboard_host": "127.0.0.1", "dashboard_port": 8090,
    "primary_topic": "/dji/primary/image_raw", "fpv_topic": "/dji/fpv/image_raw",
    "navigation_topic": "/dji/navigation/state",
    "diagnostics_topic": "/dji/diagnostics",
    "transport_metrics_topic": "/dji/edge/transport_metrics",
}


class UdpEndpoint(threading.Thread):
    """One category-specific Android UDP receiver."""

    def __init__(self, node, category, port):
        super().__init__(daemon=True)
        self.node, self.category, self.port = node, category, port
        self.stop_event = threading.Event()
        self.sock = None

    def run(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.2)
        try:
            self.sock.bind((self.node.bind_host, self.port))
        except OSError as error:
            self.node.endpoint_errors[self.port] = str(error)
            self.node.get_logger().error(f"UDP {self.port} unavailable: {error}")
            return
        while not self.stop_event.is_set():
            try:
                data, remote = self.sock.recvfrom(self.node.max_json_bytes + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            self.node.ingest(self.category, data, remote, time.monotonic_ns(), self.sock)

    def close(self):
        self.stop_event.set()
        if self.sock is not None:
            self.sock.close()
        self.join(timeout=2)


class VideoFeed:
    """Direct RTP/H.264 pipeline, ROS image publisher, and Edge metrics."""

    def __init__(self, node, name, port, topic, capture_path):
        self.node, self.name = node, name
        self.decoded_frames = self.published_frames = self.dropped_frames = 0
        self.width = self.height = 0
        self.error = None
        self.pipeline = self.gst = None
        self._stopping = threading.Event()
        self._latest_frame = LatestFrameBuffer()
        self._sample_handler_id = None
        self._ros_consumer_active = False
        self.metrics = RtpMetrics(node.rtp_payload_type)
        self.capture = RawRtpCapture(capture_path)
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.publisher = node.create_publisher(Image, topic, qos)
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            self.gst = Gst
            Gst.init(None)
            self.pipeline = Gst.parse_launch(self._description(port))
            self._sink = self.pipeline.get_by_name(f"{name}_sink")
            self._sample_handler_id = self._sink.connect("new-sample", self._on_sample)
            source = self.pipeline.get_by_name(f"{name}_source")
            source.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, self._on_rtp)
            self.pipeline.set_state(Gst.State.PLAYING)
        except Exception as error:
            self.error = str(error)
            node.get_logger().error(f"{name} video unavailable: {error}")

    def _description(self, port):
        preview = (
            "queue leaky=downstream max-size-buffers=1 ! videoconvert ! ximagesink sync=false"
            if self.node.preview_windows else "fakesink sync=false"
        )
        return (
            f"udpsrc name={self.name}_source address={self.node.bind_host} port={port} buffer-size=4194304 "
            "caps=application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,"
            f"payload={self.node.rtp_payload_type} ! rtpjitterbuffer latency={self.node.rtp_latency_ms} "
            "drop-on-latency=true do-lost=true ! rtph264depay wait-for-keyframe=true request-keyframe=true ! "
            f"h264parse config-interval=-1 ! avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! tee name={self.name}_decoded "
            f"{self.name}_decoded. ! queue leaky=downstream max-size-buffers=1 ! "
            f"video/x-raw,format=BGR ! appsink name={self.name}_sink emit-signals=true max-buffers=1 "
            f"drop=true sync=false {self.name}_decoded. ! {preview}"
        )

    def _on_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None or self._stopping.is_set():
            return self.gst.FlowReturn.OK
        caps = sample.get_caps().get_structure(0)
        width, height = caps.get_value("width"), caps.get_value("height")
        self.decoded_frames += 1
        self.width, self.height = width, height
        if not self._ros_consumer_active:
            return self.gst.FlowReturn.OK
        buffer = sample.get_buffer()
        mapped_ok, mapped = buffer.map(self.gst.MapFlags.READ)
        if mapped_ok:
            try:
                frame = (width, height, bytes(mapped.data), time.monotonic_ns())
                if self._latest_frame.put(frame):
                    self.dropped_frames += 1
            finally:
                buffer.unmap(mapped)
        return self.gst.FlowReturn.OK

    def publish_latest(self):
        """Publish one newest frame from the ROS thread, never from GStreamer."""
        if self._stopping.is_set() or not self._ros_consumer_active:
            return
        frame = self._latest_frame.take()
        if frame is None or not rclpy.ok():
            return
        width, height, data, _received_ns = frame
        image = Image()
        image.header.stamp = self.node.get_clock().now().to_msg()
        image.header.frame_id = f"dji_{self.name}_camera"
        image.width, image.height, image.encoding, image.step = width, height, "bgr8", width * 3
        image.data = data
        try:
            self.publisher.publish(image)
            self.published_frames += 1
        except RuntimeError:
            if not self._stopping.is_set():
                raise

    def set_ros_consumer_active(self, active):
        """Called only by the ROS executor to gate expensive pixel copies."""
        self._ros_consumer_active = active
        if not active:
            self._latest_frame.clear()

    def _on_rtp(self, _pad, info):
        buffer = info.get_buffer()
        if buffer is not None:
            data = buffer.extract_dup(0, buffer.get_size())
            if self.metrics.observe(data) is not None:
                self.capture.write(data)
        return self.gst.PadProbeReturn.OK

    def close(self):
        self._stopping.set()
        self._latest_frame.clear()
        if self.pipeline is not None:
            if self._sample_handler_id is not None:
                self._sink.disconnect(self._sample_handler_id)
                self._sample_handler_id = None
            self.pipeline.set_state(self.gst.State.NULL)
        self.capture.close()


class EdgeBridge(Node):
    def __init__(self):
        super().__init__("dji_edge_driver")
        for name, default in PARAMETERS.items():
            self.declare_parameter(name, default)
        self.bind_host = self.parameter("bind_host")
        self.max_json_bytes = self.parameter("max_json_bytes")
        self.rtp_payload_type = self.parameter("rtp_payload_type")
        self.rtp_latency_ms = self.parameter("rtp_latency_ms")
        self.preview_windows = self.parameter("preview_windows")

        self.navigation_publisher = self.create_publisher(NavigationState, self.parameter("navigation_topic"), 10)
        self.diagnostics_publisher = self.create_publisher(DiagnosticArray, self.parameter("diagnostics_topic"), 10)
        self.metrics_publisher = self.create_publisher(DiagnosticArray, self.parameter("transport_metrics_topic"), 10)
        self.evidence = EvidenceWriter(create_session_directory(self.parameter("evidence_dir")))
        self.clock_mapper = ClockMapper()
        self.latest_state = LatestState(self.clock_mapper)
        self.sequences = SequenceTracker()
        self.accepted = self.rejected = self.clock_pongs = self.clock_sequence = 0
        self.endpoint_errors = {}

        self.inputs = [
            UdpEndpoint(self, "telemetry", self.parameter("telemetry_port")),
            UdpEndpoint(self, "frame_metadata", self.parameter("frame_metadata_port")),
            UdpEndpoint(self, "clock", self.parameter("clock_port")),
        ]
        for endpoint in self.inputs:
            endpoint.start()
        self.videos = self._create_videos()
        self.create_timer(0.1, self.refresh_video_subscriptions)
        self.create_timer(1.0 / 60.0, self.publish_latest_frames)
        self.create_timer(1.0, self.publish_diagnostics)
        self.clock_stop = threading.Event()
        self.clock_host = self.parameter("android_clock_host")
        self.clock_port = self.parameter("android_clock_port")
        self.clock_interval = self.parameter("clock_ping_interval_s")
        self.clock_thread = threading.Thread(target=self._clock_loop, daemon=True)
        self.clock_thread.start()
        self.stop_requested = threading.Event()
        self.dashboard = self._create_dashboard()

    def parameter(self, name):
        return self.get_parameter(name).value

    def _create_videos(self):
        if not self.parameter("publish_video"):
            return []
        capture = self.parameter("capture_rtp")
        return [
            VideoFeed(self, "primary", self.parameter("primary_rtp_port"), self.parameter("primary_topic"), str(self.evidence.root / "primary.rtpbin") if capture else None),
            VideoFeed(self, "fpv", self.parameter("fpv_rtp_port"), self.parameter("fpv_topic"), str(self.evidence.root / "fpv.rtpbin") if capture else None),
        ]

    def _create_dashboard(self):
        if not self.parameter("dashboard_enabled"):
            return None
        host = self.parameter("dashboard_host")
        dashboard = DashboardServer(host, self.parameter("dashboard_port"), self.dashboard_state, self.stop_requested.set)
        dashboard.start()
        self.get_logger().info(f"dashboard=http://{host}:{dashboard.port}/")
        return dashboard

    def ingest(self, category, data, remote, edge_receive_ns, response_socket):
        try:
            packet = decode_json_packet(data, expected_version=1, max_bytes=self.max_json_bytes)
            if category == "telemetry" and packet.packet_type in FRAME_TYPES | CLOCK_TYPES:
                raise ProtocolError(f"{packet.packet_type} is not telemetry")
            if category == "frame_metadata" and packet.packet_type not in FRAME_TYPES:
                raise ProtocolError(f"{packet.packet_type} is not frame metadata")
            if category == "clock" and packet.packet_type not in CLOCK_TYPES:
                raise ProtocolError(f"{packet.packet_type} is not a clock packet")
        except ProtocolError as error:
            self._reject(data, remote, edge_receive_ns, str(error))
            return
        if category == "clock":
            self._ingest_clock(packet, remote, edge_receive_ns, response_socket)
            return
        sequence = self.sequences.observe((packet.session, packet.packet_type, packet.stream), packet.sequence)
        self.latest_state.update_packet(packet, edge_receive_ns, remote, sequence)
        if not sequence.is_newest:
            return
        self.accepted += 1
        record = {
            "session": packet.session, "type": packet.packet_type, "stream": packet.stream,
            "sequence": packet.sequence, "android_mono_ns": packet.android_mono_ns,
            "edge_receive_mono_ns": edge_receive_ns, "remote": f"{remote[0]}:{remote[1]}",
            "data": packet.raw.get("data", packet.raw),
        }
        self.evidence.write("frame_metadata" if packet.packet_type in FRAME_TYPES else "telemetry", record)
        if packet.packet_type in {"flight", "rtk", "gimbal"}:
            self.publish_navigation()

    def _reject(self, data, remote, edge_receive_ns, error):
        self.rejected += 1
        self.latest_state.reject()
        self.evidence.write("protocol_errors", {"edge_receive_mono_ns": edge_receive_ns, "remote": f"{remote[0]}:{remote[1]}", "error": error, "raw": data.decode("utf-8", errors="replace")})

    def _ingest_clock(self, packet, remote, edge_receive_ns, response_socket):
        try:
            raw = packet.raw
            if packet.packet_type == "clock_ping":
                response = {"v": 1, "type": "clock_pong", "session": packet.session, "stream": packet.stream, "seq": packet.sequence, "t0_edge_send_mono_ns": raw["t0_edge_send_mono_ns"], "t1_android_rx_mono_ns": edge_receive_ns, "t2_android_tx_mono_ns": time.monotonic_ns()}
                response_socket.sendto(json.dumps(response, separators=(",", ":")).encode(), remote)
                return
            t0, t1, t2 = (int(raw[name]) for name in ("t0_edge_send_mono_ns", "t1_android_rx_mono_ns", "t2_android_tx_mono_ns"))
            sample = self.clock_mapper.add_exchange(t0, t1, t2, edge_receive_ns)
            self.clock_pongs += 1
            self.evidence.write("clock", {**sample.as_dict(), "estimate": self.clock_mapper.estimate(), "remote": f"{remote[0]}:{remote[1]}"})
        except (KeyError, TypeError, ValueError) as error:
            self._reject(b"", remote, edge_receive_ns, f"invalid clock pong: {error}")

    def _clock_loop(self):
        if not self.clock_host:
            return
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as clock_socket:
            while not self.clock_stop.wait(self.clock_interval):
                self.clock_sequence += 1
                payload = {"v": 1, "type": "clock_ping", "session": "edge-clock", "stream": "clock", "seq": self.clock_sequence, "t0_edge_send_mono_ns": time.monotonic_ns()}
                try:
                    clock_socket.sendto(json.dumps(payload, separators=(",", ":")).encode(), (self.clock_host, self.clock_port))
                except OSError as error:
                    self.endpoint_errors["clock_pinger"] = str(error)

    def publish_navigation(self):
        navigation = build_navigation(self.latest_state.snapshot())
        if navigation is None:
            return
        message = NavigationState()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = "wgs84"
        message.latitude_deg, message.longitude_deg, message.altitude_m = navigation["latitude_deg"], navigation["longitude_deg"], navigation["altitude_m"]
        message.heading_deg = navigation["heading_deg"]
        message.position_source = NavigationState.POSITION_RTK if navigation["position_source"] == "rtk" else NavigationState.POSITION_GPS_FALLBACK
        message.position_valid, message.rtk_valid = navigation["position_valid"], navigation["rtk_valid"]
        message.gimbal_pitch_valid, message.gimbal_pitch_deg, message.gimbal_android_mono_ns = navigation["gimbal_pitch_valid"], navigation["gimbal_pitch_deg"], navigation["gimbal_android_mono_ns"]
        message.session, message.android_mono_ns, message.edge_receive_mono_ns = navigation["session"], navigation["android_mono_ns"], navigation["edge_receive_mono_ns"]
        message.transport_age_s = float("nan") if navigation["transport_age_s"] is None else navigation["transport_age_s"]
        self.navigation_publisher.publish(message)

    def publish_latest_frames(self):
        for video in self.videos:
            video.publish_latest()

    def refresh_video_subscriptions(self):
        for video in self.videos:
            video.set_ros_consumer_active(video.publisher.get_subscription_count() > 0)

    def publish_diagnostics(self):
        evidence = self.evidence.health()
        status = DiagnosticStatus(name="dji_edge_driver/direct", level=DiagnosticStatus.OK if self.accepted and not self.endpoint_errors else DiagnosticStatus.WARN, message="direct Android ingress")
        status.values = [KeyValue(key="post_network.accepted", value=str(self.accepted)), KeyValue(key="post_network.rejected", value=str(self.rejected)), KeyValue(key="clock.pongs", value=str(self.clock_pongs)), KeyValue(key="evidence.path", value=str(self.evidence.root)), KeyValue(key="evidence.write_errors", value=str(evidence["write_errors"])), KeyValue(key="evidence.dropped_records", value=str(evidence["dropped_records"])), KeyValue(key="legacy_http_polling", value="disabled")]
        for port, error in self.endpoint_errors.items():
            status.values.append(KeyValue(key=f"udp.{port}.error", value=error))
        for video in self.videos:
            metrics = video.metrics.snapshot()
            status.values.extend([KeyValue(key=f"{video.name}.decoded_frames", value=str(video.decoded_frames)), KeyValue(key=f"{video.name}.published_frames", value=str(video.published_frames)), KeyValue(key=f"{video.name}.dropped_old_frames", value=str(video.dropped_frames)), KeyValue(key=f"{video.name}.resolution", value=f"{video.width}x{video.height}"), KeyValue(key=f"{video.name}.error", value=video.error or ""), KeyValue(key=f"{video.name}.rtp_packets", value=str(metrics["packets_received"])), KeyValue(key=f"{video.name}.rtp_gaps", value=str(metrics["sequence_gaps"])), KeyValue(key=f"{video.name}.fps", value=str(metrics["estimated_fps"]))])
        diagnostics = DiagnosticArray()
        diagnostics.header.stamp = self.get_clock().now().to_msg()
        diagnostics.status = [status]
        self.diagnostics_publisher.publish(diagnostics)
        self.metrics_publisher.publish(diagnostics)

    def dashboard_state(self):
        evidence = self.evidence.health()
        videos = [{"name": video.name, "decoded_frames": video.decoded_frames, "published_frames": video.published_frames, "dropped_old_frames": video.dropped_frames, "resolution": {"width": video.width, "height": video.height}, "error": video.error, "rtp": video.metrics.snapshot(), "capture_rtp": video.capture.enabled} for video in self.videos]
        state = self.latest_state.snapshot()
        return {
            "schema_version": 1,
            "status": "ok" if not self.endpoint_errors else "degraded",
            "evidence": {"path": str(self.evidence.root), **evidence},
            "clock": self.clock_mapper.estimate(),
            "configuration": {
                "bind_host": self.bind_host,
                "android_clock_host": self.clock_host or None,
                "rtp_latency_ms": self.rtp_latency_ms,
                "preview_windows": self.preview_windows,
                "capture_rtp": self.parameter("capture_rtp"),
                "primary_topic": self.parameter("primary_topic"),
                "fpv_topic": self.parameter("fpv_topic"),
                "navigation_topic": self.parameter("navigation_topic"),
            },
            "android_pre_network": state["health"],
            "edge_post_network": {"transport": state["transport"], "video": videos},
            "transport": state["transport"],
            "navigation": build_navigation(state),
            "video": videos,
            "udp_errors": self.endpoint_errors,
        }

    def destroy_node(self):
        self.clock_stop.set()
        self.clock_thread.join(timeout=2)
        if self.dashboard is not None:
            self.dashboard.close()
        for endpoint in self.inputs:
            endpoint.close()
        for video in self.videos:
            video.close()
        self.evidence.close()
        return super().destroy_node()


def main():
    rclpy.init()
    node = EdgeBridge()
    try:
        while rclpy.ok() and not node.stop_requested.is_set():
            rclpy.spin_once(node, timeout_sec=0.2)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        try:
            node.destroy_node()
        except KeyboardInterrupt:
            # A launch supervisor can deliver a second SIGINT while its children
            # are already draining. The VideoFeed stop gate has already made
            # callbacks harmless, so preserve the remaining process cleanup.
            pass
        try:
            if rclpy.ok():
                rclpy.shutdown()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
