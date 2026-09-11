#!/usr/bin/env python3
"""Direct Android UDP/RTP ingestion; no HTTP polling or loopback relay."""

import json
from pathlib import Path
import socket
import threading
import time

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from dji_edge_driver.msg import FrameContext, NavigationState
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String

from dji_edge_transport_core.clock import ClockMapper
from dji_edge_transport_core.clock_client import ClockPinger
from dji_edge_transport_core.dashboard import start_optional_dashboard
from dji_edge_transport_core.evidence import EvidenceWriter, create_session_directory
from dji_edge_transport_core.local_config import resolve_ipv4_udp_target, save_requested_config
from dji_edge_transport_core.mapper_config import DEFAULT_VALUES as MAPPER_DEFAULT_VALUES
from dji_edge_transport_core.mapper_config import load as load_mapper_config
from dji_edge_transport_core.mapper_config import save_requested_config as save_mapper_config
from dji_edge_transport_core.mapper_status import MapperStatusCache
from dji_edge_transport_core.navigation import build_navigation, triggers_navigation
from dji_edge_transport_core.protocol import CLOCK_TYPES, FRAME_TYPES, ProtocolError, decode_json_packet
from dji_edge_transport_core.state import IngressMetrics, LatestState, SequenceTracker
from dji_edge_transport_core.feed_pipeline import FeedPipeline


PARAMETERS = {
    "bind_host": "0.0.0.0", "telemetry_port": 5500, "frame_metadata_port": 5501,
    "clock_port": 5502, "primary_rtp_port": 5600, "fpv_rtp_port": 5610,
    "max_json_bytes": 1200, "rtp_payload_type": 96, "rtp_latency_ms": 20,
    "preview_windows": False, "publish_video": True, "capture_rtp": False,
    "evidence_dir": "evidence", "android_clock_host": "", "android_clock_port": 5502,
    "clock_ping_interval_s": 1.0, "clock_response_timeout_s": 0.5, "dashboard_enabled": True,
    "dashboard_host": "127.0.0.1", "dashboard_port": 8090,
    "primary_topic": "/dji/primary/image_raw", "fpv_topic": "/dji/fpv/image_raw",
    "primary_context_topic": "/dji/primary/frame_context", "fpv_context_topic": "/dji/fpv/frame_context",
    "navigation_topic": "/dji/navigation/state",
    "mapper_status_topic": "/dji/navigation/status", "mapper_status_stale_s": 3.0,
    "diagnostics_topic": "/dji/diagnostics",
    "transport_metrics_topic": "/dji/edge/transport_metrics",
    "local_config_path": "/workspace/bridge.local.yaml", "mapper_runtime_config_path": "/workspace/mapper.runtime.local.yaml",
    "restart_request_path": "/workspace/.runtime/dji-edge-restart.request",
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
    """ROS-facing wrapper around one bounded, restartable feed pipeline."""

    def __init__(self, node, name, port, topic, context_topic, capture_path, ros_publish_enabled):
        self.node, self.name = node, name
        self.published_frames = 0
        self.ros_publish_enabled = ros_publish_enabled
        self.context_published = self.context_unavailable = 0
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.publisher = node.create_publisher(Image, topic, qos)
        self.context_publisher = node.create_publisher(FrameContext, context_topic, qos)

        self.pipeline_service = FeedPipeline(
            name=name,
            port=port,
            bind_host=node.bind_host,
            payload_type=node.rtp_payload_type,
            latency_ms=node.rtp_latency_ms,
            preview_windows=node.preview_windows,
            capture_path=capture_path,
            logger=node.get_logger().error,
        )

    @property
    def decoded_frames(self):
        return self.pipeline_service.decoded_frames

    @property
    def width(self):
        return self.pipeline_service.width

    @property
    def height(self):
        return self.pipeline_service.height

    @property
    def error(self):
        return self.pipeline_service.error

    @property
    def metrics(self):
        return self.pipeline_service.metrics

    @property
    def dropped_frames(self):
        return self.pipeline_service.dropped_old_frames

    @property
    def capture(self):
        return self.pipeline_service.capture

    @property
    def _pts_binding(self):
        return self.pipeline_service._pts_binding

    def maintain(self):
        self.pipeline_service.maintain()

    def snapshot(self):
        return self.pipeline_service.snapshot()

    def publish_latest(self):
        """Publish one newest frame from the ROS thread, never from GStreamer."""
        if not self.pipeline_service._ros_consumer_active:
            return
        frame = self.pipeline_service.take_latest()
        if frame is None or not rclpy.ok():
            return
        width, height, data, decoded_ns, rtp_identity = frame.width, frame.height, frame.data, frame.decoded_mono_ns, frame.rtp_identity
        image = Image()
        image.header.stamp = self.node.get_clock().now().to_msg()
        image.header.frame_id = f"dji_{self.name}_camera"
        image.width, image.height, image.encoding, image.step = width, height, "bgr8", width * 3
        image.data = data
        try:
            self.publisher.publish(image)
            context = self.node.frame_context(self.name, rtp_identity, image.header, decoded_ns)
            self.context_publisher.publish(context)
            self.node.record_frame_context(context)
            self.context_published += 1
            if context.association_quality == FrameContext.ASSOCIATION_UNAVAILABLE:
                self.context_unavailable += 1
            self.published_frames += 1
        except RuntimeError:
            if not self.pipeline_service._stopping.is_set():
                raise

    def set_ros_consumer_active(self, active):
        """Called only by the ROS executor to gate expensive pixel copies."""
        self.pipeline_service.set_ros_consumer_active(self.ros_publish_enabled and active)

    def close(self):
        self.pipeline_service.close()


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
        self.ingress = IngressMetrics(("telemetry", "frame_metadata", "clock"))
        self.accepted = self.rejected = self.clock_pongs = self.clock_sequence = 0
        self.endpoint_errors = {}
        self.mapper_status = MapperStatusCache()
        mapper_status_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.mapper_status_subscription = self.create_subscription(
            String, self.parameter("mapper_status_topic"), self._on_mapper_status, mapper_status_qos,
        )

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
        self.create_timer(0.25, self.maintain_video_pipelines)
        self.create_timer(1.0, self.publish_diagnostics)
        self.clock_stop = threading.Event()
        self.clock_host = self.parameter("android_clock_host")
        self.clock_port = self.parameter("android_clock_port")
        self.clock_interval = self.parameter("clock_ping_interval_s")
        self.clock_pinger = ClockPinger(self.parameter("clock_response_timeout_s"), self.max_json_bytes)
        self.clock_thread = threading.Thread(target=self._clock_loop, daemon=True)
        self.clock_thread.start()
        self.stop_requested = threading.Event()
        self.dashboard = self._create_dashboard()

    def parameter(self, name):
        return self.get_parameter(name).value

    def _create_videos(self):
        capture = self.parameter("capture_rtp")
        ros_publish_enabled = self.parameter("publish_video")
        return [
            VideoFeed(self, "primary", self.parameter("primary_rtp_port"), self.parameter("primary_topic"), self.parameter("primary_context_topic"), str(self.evidence.root / "primary.rtpbin") if capture else None, ros_publish_enabled),
            VideoFeed(self, "fpv", self.parameter("fpv_rtp_port"), self.parameter("fpv_topic"), self.parameter("fpv_context_topic"), str(self.evidence.root / "fpv.rtpbin") if capture else None, ros_publish_enabled),
        ]

    def _create_dashboard(self):
        if not self.parameter("dashboard_enabled"):
            return None
        host = self.parameter("dashboard_host")
        dashboard = start_optional_dashboard(
            host,
            self.parameter("dashboard_port"),
            self.dashboard_state,
            self.stop_requested.set,
            self.dashboard_config,
            self.save_dashboard_config,
            self.mapper_dashboard_config,
            self.save_mapper_dashboard_config,
            on_error=lambda error: self._record_dashboard_error(host, error),
        )
        if dashboard is not None:
            self.get_logger().info(f"dashboard=http://{host}:{dashboard.port}/")
        return dashboard

    def _record_dashboard_error(self, host, error):
        self.endpoint_errors["dashboard"] = str(error)
        self.get_logger().error(f"dashboard unavailable at http://{host}:{self.parameter('dashboard_port')}/: {error}")

    def dashboard_config(self):
        addresses = set()
        try:
            for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                if family == socket.AF_INET and not sockaddr[0].startswith("127."):
                    addresses.add(sockaddr[0])
        except OSError:
            pass
        return {
            "source": self.parameter("local_config_path") if Path(self.parameter("local_config_path")).exists() else "committed defaults",
            "values": {
                "android_clock_host": self.clock_host,
                "capture_rtp": self.parameter("capture_rtp"),
                "preview_windows": self.preview_windows,
                "publish_video": self.parameter("publish_video"),
            },
            "ubuntu_ipv4": sorted(addresses),
        }

    def save_dashboard_config(self, values, restart):
        return save_requested_config(
            values,
            self.parameter("local_config_path"),
            self.parameter("restart_request_path"),
            restart=restart,
            request_stop=self.stop_requested.set,
        )

    def _on_mapper_status(self, message):
        """Cache existing one-hertz mapper status; never touch transport ingress."""
        self.mapper_status.observe(message.data)

    def mapper_dashboard_config(self):
        path = Path(self.parameter("mapper_runtime_config_path"))
        try:
            requested_values = load_mapper_config(path)
            config_error = None
        except (OSError, ValueError) as error:
            requested_values = None
            config_error = str(error)
        status = self.mapper_status.snapshot(float(self.parameter("mapper_status_stale_s")))
        active_values = status["active_values"]
        values = requested_values or active_values or MAPPER_DEFAULT_VALUES
        return {
            "source": str(path) if path.exists() else "mapper defaults or private mapper.local.yaml",
            "values": values,
            "requested_values": requested_values,
            "active_values": active_values,
            "pending_restart": requested_values is not None and requested_values != active_values,
            "status": {**status, "error": config_error or status["error"]},
        }

    def save_mapper_dashboard_config(self, values, restart):
        return save_mapper_config(
            values,
            self.parameter("mapper_runtime_config_path"),
            self.parameter("restart_request_path"),
            restart=restart,
            request_stop=self.stop_requested.set,
        )

    def ingest(self, category, data, remote, edge_receive_ns, response_socket):
        self.ingress.observe(category, edge_receive_ns)
        try:
            packet = decode_json_packet(data, expected_version=1, max_bytes=self.max_json_bytes)
            if category == "telemetry" and packet.packet_type in FRAME_TYPES | CLOCK_TYPES:
                raise ProtocolError(f"{packet.packet_type} is not telemetry")
            if category == "frame_metadata" and packet.packet_type not in FRAME_TYPES:
                raise ProtocolError(f"{packet.packet_type} is not frame metadata")
            if category == "clock" and packet.packet_type not in CLOCK_TYPES:
                raise ProtocolError(f"{packet.packet_type} is not a clock packet")
        except ProtocolError as error:
            self._reject(category, data, remote, edge_receive_ns, str(error))
            return
        if category == "clock":
            self._ingest_clock(packet, remote, edge_receive_ns, response_socket)
            return
        self.ingress.accept(category)
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
        if triggers_navigation(packet.packet_type):
            self.publish_navigation()

    def _reject(self, category, data, remote, edge_receive_ns, error):
        self.rejected += 1
        self.ingress.reject(category)
        self.latest_state.reject()
        self.evidence.write("protocol_errors", {"edge_receive_mono_ns": edge_receive_ns, "remote": f"{remote[0]}:{remote[1]}", "error": error, "raw": data.decode("utf-8", errors="replace")})

    def _ingest_clock(self, packet, remote, edge_receive_ns, response_socket):
        try:
            raw = packet.raw
            if packet.packet_type == "clock_ping":
                response = {"v": 1, "type": "clock_pong", "session": packet.session, "stream": packet.stream, "seq": packet.sequence, "t0_edge_send_mono_ns": raw["t0_edge_send_mono_ns"], "t1_android_rx_mono_ns": edge_receive_ns, "t2_android_tx_mono_ns": time.monotonic_ns()}
                response_socket.sendto(json.dumps(response, separators=(",", ":")).encode(), remote)
                self.ingress.accept("clock")
                return
            self._record_clock_pong(raw, remote, edge_receive_ns)
            self.ingress.accept("clock")
        except (KeyError, TypeError, ValueError) as error:
            self._reject("clock", b"", remote, edge_receive_ns, f"invalid clock pong: {error}")

    def _record_clock_pong(self, raw, remote, edge_receive_ns):
        t0, t1, t2 = (int(raw[name]) for name in ("t0_edge_send_mono_ns", "t1_android_rx_mono_ns", "t2_android_tx_mono_ns"))
        sample = self.clock_mapper.add_exchange(t0, t1, t2, edge_receive_ns)
        self.clock_pongs += 1
        self.evidence.write("clock", {**sample.as_dict(), "estimate": self.clock_mapper.estimate(), "remote": f"{remote[0]}:{remote[1]}"})

    def _clock_loop(self):
        if not self.clock_host:
            return
        while not self.clock_stop.wait(self.clock_interval):
            self.clock_sequence += 1
            try:
                target = resolve_ipv4_udp_target(self.clock_host, self.clock_port)
                def matches_ping(exchange):
                    try:
                        response = decode_json_packet(exchange.response_data, expected_version=1, max_bytes=self.max_json_bytes)
                    except ProtocolError:
                        return False
                    return (
                        response.packet_type == "clock_pong" and response.session == "edge-clock"
                        and response.stream == "clock" and response.sequence == self.clock_sequence
                        and response.raw.get("t0_edge_send_mono_ns") == exchange.t0_edge_send_mono_ns
                        and exchange.remote[0] == target[0]
                    )

                exchange = self.clock_pinger.exchange_once(target, "edge-clock", self.clock_sequence, matches_ping)
                response = decode_json_packet(exchange.response_data, expected_version=1, max_bytes=self.max_json_bytes)
                self._record_clock_pong(response.raw, exchange.remote, exchange.t3_edge_receive_mono_ns)
                self.endpoint_errors.pop("clock_pinger", None)
            except (OSError, ProtocolError, ValueError) as error:
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

    def frame_context(self, feed, rtp_identity, header, decoded_ns):
        message = FrameContext()
        message.header = header
        message.feed = feed
        message.edge_decoded_mono_ns = decoded_ns
        identity_and_navigation = None if rtp_identity is None else self.latest_state.associate_frame(feed, *rtp_identity)
        if identity_and_navigation is None:
            message.association_quality = FrameContext.ASSOCIATION_UNAVAILABLE
            message.association_reason = "missing or ambiguous RTP/AU association"
            return message
        identity, navigation = identity_and_navigation
        message.session, message.frame_seq = identity["session"], identity["frame_seq"]
        message.rtp_ssrc, message.rtp_ts = identity["rtp_ssrc"], identity["rtp_ts"]
        message.android_au_first_byte_mono_ns = identity["android_first_byte_mono_ns"]
        message.android_au_complete_mono_ns = identity["android_complete_mono_ns"]
        message.has_dji_source_timestamp = identity["dji_source_timestamp_ns"] is not None
        message.dji_source_timestamp_ns = identity["dji_source_timestamp_ns"] or 0
        message.dji_timestamp_source = identity["dji_timestamp_source"] or ""
        message.association_quality = {
            "interpolated": FrameContext.ASSOCIATION_INTERPOLATED,
            "nearest": FrameContext.ASSOCIATION_NEAREST,
        }.get(navigation["association_quality"], FrameContext.ASSOCIATION_UNAVAILABLE)
        message.association_reason = navigation["association_reason"]
        message.navigation_time_offset_ns = navigation["navigation_time_offset_ns"]
        message.position_valid = navigation["position_valid"]
        message.position_source = {
            "rtk": FrameContext.POSITION_RTK,
            "gps_fallback": FrameContext.POSITION_GPS_FALLBACK,
        }.get(navigation["position_source"], FrameContext.POSITION_UNKNOWN)
        message.rtk_valid = navigation["rtk_valid"]
        message.latitude_deg, message.longitude_deg, message.altitude_m, message.heading_deg = (navigation[key] for key in ("latitude_deg", "longitude_deg", "altitude_m", "heading_deg"))
        message.gimbal_pitch_valid, message.gimbal_pitch_deg = navigation["gimbal_pitch_valid"], navigation["gimbal_pitch_deg"]
        message.flight_android_mono_ns, message.rtk_android_mono_ns, message.gimbal_android_mono_ns = (navigation[key] for key in ("flight_android_mono_ns", "rtk_android_mono_ns", "gimbal_android_mono_ns"))
        return message

    def record_frame_context(self, message):
        self.evidence.write("frame_context", {
            "session": message.session, "feed": message.feed, "frame_seq": message.frame_seq,
            "rtp_ssrc": message.rtp_ssrc, "rtp_ts": message.rtp_ts,
            "source_time": {
                "android_au_first_byte_mono_ns": message.android_au_first_byte_mono_ns,
                "android_au_complete_mono_ns": message.android_au_complete_mono_ns,
                "dji_source_timestamp_ns": message.dji_source_timestamp_ns if message.has_dji_source_timestamp else None,
                "dji_timestamp_source": message.dji_timestamp_source if message.has_dji_source_timestamp else None,
            },
            "association": {
                "quality": int(message.association_quality), "reason": message.association_reason,
                "navigation_time_offset_ns": message.navigation_time_offset_ns,
                "position_valid": message.position_valid, "position_source": int(message.position_source),
                "rtk_valid": message.rtk_valid, "gimbal_pitch_valid": message.gimbal_pitch_valid,
            },
            "edge_observation": {"decoded_mono_ns": message.edge_decoded_mono_ns},
        })

    def publish_latest_frames(self):
        for video in self.videos:
            video.publish_latest()

    def maintain_video_pipelines(self):
        for video in self.videos:
            video.maintain()

    def refresh_video_subscriptions(self):
        for video in self.videos:
            video.set_ros_consumer_active(video.publisher.get_subscription_count() > 0)

    def publish_diagnostics(self):
        evidence = self.evidence.health()
        status = DiagnosticStatus(name="dji_edge_driver/direct", level=DiagnosticStatus.OK if self.accepted and not self.endpoint_errors else DiagnosticStatus.WARN, message="direct Android ingress")
        status.values = [KeyValue(key="post_network.accepted", value=str(self.accepted)), KeyValue(key="post_network.rejected", value=str(self.rejected)), KeyValue(key="clock.pongs", value=str(self.clock_pongs)), KeyValue(key="evidence.path", value=str(self.evidence.root)), KeyValue(key="evidence.write_errors", value=str(evidence["write_errors"])), KeyValue(key="evidence.dropped_records", value=str(evidence["dropped_records"])), KeyValue(key="legacy_http_polling", value="disabled")]
        video_evidence = []
        for port, error in self.endpoint_errors.items():
            status.values.append(KeyValue(key=f"udp.{port}.error", value=error))
        for video in self.videos:
            metrics = video.metrics.snapshot()
            binding = video._pts_binding.snapshot()
            feed = video.snapshot()
            status.values.extend([
                KeyValue(key=f"{video.name}.decoded_frames", value=str(video.decoded_frames)),
                KeyValue(key=f"{video.name}.published_frames", value=str(video.published_frames)),
                KeyValue(key=f"{video.name}.dropped_old_frames", value=str(video.dropped_frames)),
                KeyValue(key=f"{video.name}.context_published", value=str(video.context_published)),
                KeyValue(key=f"{video.name}.context_unavailable", value=str(video.context_unavailable)),
                KeyValue(key=f"{video.name}.pts_binding_missing", value=str(binding["missing"])),
                KeyValue(key=f"{video.name}.resolution", value=f"{video.width}x{video.height}"),
                KeyValue(key=f"{video.name}.error", value=video.error or ""),
                KeyValue(key=f"{video.name}.status", value=self._feed_status(video.name, feed)),
                KeyValue(key=f"{video.name}.last_rtp_age_s", value=str(metrics["last_datagram_age_s"])),
                KeyValue(key=f"{video.name}.last_decoded_age_s", value=str(feed["last_decoded_age_s"])),
                KeyValue(key=f"{video.name}.restart_count", value=str(feed["restart_count"])),
                KeyValue(key=f"{video.name}.decoder_backend", value=feed["decoder"]["backend"]),
                KeyValue(key=f"{video.name}.decoder_reason", value=feed["decoder"]["reason"]),
                KeyValue(key=f"{video.name}.rtp_packets", value=str(metrics["packets_received"])),
                KeyValue(key=f"{video.name}.rtp_gaps", value=str(metrics["sequence_gaps"])),
                KeyValue(key=f"{video.name}.fps", value=str(metrics["estimated_fps"])),
            ])
            video_evidence.append({
                "name": video.name,
                "decoder": feed["decoder"],
                "decoded_frames": video.decoded_frames,
                "published_frames": video.published_frames,
                "dropped_old_frames": video.dropped_frames,
                "rtp": metrics,
            })
        self.evidence.write("transport", {
            "edge_observation": {"ingress": self.ingress_snapshot(), "video": video_evidence}
        })
        diagnostics = DiagnosticArray()
        diagnostics.header.stamp = self.get_clock().now().to_msg()
        diagnostics.status = [status]
        self.diagnostics_publisher.publish(diagnostics)
        self.metrics_publisher.publish(diagnostics)

    def dashboard_state(self):
        evidence = self.evidence.health()
        videos = []
        signals = []
        for video in self.videos:
            feed = video.snapshot()
            feed_status = self._feed_status(video.name, feed)
            signals.append(feed_status)
            videos.append({"name": video.name, "decoded_frames": video.decoded_frames, "published_frames": video.published_frames, "dropped_old_frames": video.dropped_frames, "context": {"published": video.context_published, "unavailable": video.context_unavailable, "pts_binding": video._pts_binding.snapshot()}, "resolution": {"width": video.width, "height": video.height}, "decoder": feed["decoder"], "error": video.error, "status": feed_status, "last_decoded_age_s": feed["last_decoded_age_s"], "last_bus_message": feed["last_bus_message"], "last_failure": feed["last_failure"], "last_failure_age_s": feed["last_failure_age_s"], "restart_count": feed["restart_count"], "restart_exhausted": feed["restart_exhausted"], "rtp": video.metrics.snapshot(), "capture_rtp": video.capture.enabled})
        if "clock_pinger" in self.endpoint_errors:
            signals.append("clock_timeout")
        ingress = self.ingress_snapshot()
        state = self.latest_state.snapshot()
        return {
            "schema_version": 1,
            "status": "ok" if not self.endpoint_errors and not any(video.error for video in self.videos) else "degraded",
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
                "mapper_status_topic": self.parameter("mapper_status_topic"),
            },
            "android_pre_network": state["health"],
            "edge_post_network": {"transport": state["transport"], "ingress": ingress, "video": videos},
            "ingress": ingress,
            "transport": state["transport"],
            "navigation": build_navigation(state),
            "mapper": self.mapper_dashboard_config(),
            "video": videos,
            "udp_errors": self.endpoint_errors,
            "signals": signals,
        }

    def _feed_status(self, name, snapshot):
        recent_failure = (
            snapshot["last_failure_age_s"] is not None
            and snapshot["last_failure_age_s"] <= 3.0
        )
        if recent_failure and snapshot["last_failure_preview_renderer_suspect"]:
            return "primary_preview_renderer_suspect" if name == "primary" else "fpv_preview_renderer_suspect"
        if recent_failure and str(snapshot["last_failure"]).startswith("WATCHDOG"):
            return "primary_decode_stalled" if name == "primary" else "fpv_decode_stalled"
        if recent_failure and snapshot["last_failure"]:
            return "primary_pipeline_error" if name == "primary" else "fpv_pipeline_error"
        if snapshot["restart_exhausted"]:
            return "primary_pipeline_error" if name == "primary" else "fpv_pipeline_error"
        if snapshot["preview_renderer_suspect"]:
            return "primary_preview_renderer_suspect" if name == "primary" else "fpv_preview_renderer_suspect"
        if snapshot["decode_stalled"]:
            return "primary_decode_stalled" if name == "primary" else "fpv_decode_stalled"
        if snapshot["pipeline_error"]:
            return "primary_pipeline_error" if name == "primary" else "fpv_pipeline_error"
        rtp = snapshot["rtp"]
        if rtp["last_datagram_age_s"] is None:
            if name == "fpv":
                health = self.latest_state.snapshot().get("health") or {}
                data = (health.get("data") or {}) if isinstance(health, dict) else {}
                callbacks = data.get("secondary_video_callbacks", 0)
                emitted = data.get("secondary_rtp_packets", 0)
                if isinstance(callbacks, (int, float)) and callbacks > 0 and emitted == 0:
                    return "fpv_not_emitted_by_android"
            return "waiting_for_rtp"
        if snapshot["last_decoded_age_s"] is None:
            return "waiting_for_decode"
        return "running"

    def ingress_snapshot(self):
        ingress = self.ingress.snapshot()
        for video in self.videos:
            rtp = video.metrics.snapshot()
            ingress[video.name] = {
                "datagrams_received": rtp["datagrams_observed"],
                "packets_valid": rtp["packets_received"],
                "packets_rejected": rtp["packets_rejected"],
                "last_datagram_age_s": rtp["last_datagram_age_s"],
            }
        return ingress

    def destroy_node(self):
        self.clock_stop.set()
        self.clock_thread.join(timeout=2)
        self.clock_pinger.close()
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
