#!/usr/bin/env python3
"""Bounded, loss-tolerant ROS adapter for the edge receiver's local contract."""

import json
import math
import time
from urllib.error import URLError
from urllib.request import urlopen

import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from dji_edge_bridge.msg import NavigationState
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String


def unwrap(fields, name, default=None):
    value = fields.get(name, default)
    return value.get("value", default) if isinstance(value, dict) else value


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class VideoReceiver:
    def __init__(self, node, name, port, topic, payload_type, latency_ms):
        self.node, self.name = node, name
        self.publisher = node.create_publisher(Image, topic, QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self.pipeline = None
        self.Gst = None
        self._start(port, payload_type, latency_ms)

    def _start(self, port, payload_type, latency_ms):
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            self.Gst = Gst
            Gst.init(None)
            description = (
                f"udpsrc port={port} buffer-size=2097152 caps=application/x-rtp,media=video,encoding-name=H264,"
                f"clock-rate=90000,payload={payload_type} ! rtpjitterbuffer latency={latency_ms} drop-on-latency=true do-lost=true ! "
                "rtph264depay wait-for-keyframe=true request-keyframe=true ! h264parse config-interval=-1 ! "
                "avdec_h264 max-threads=2 ! videoconvert ! video/x-raw,format=BGR ! "
                f"appsink name={self.name}_sink emit-signals=true max-buffers=1 drop=true sync=false"
            )
            self.pipeline = Gst.parse_launch(description)
            sink = self.pipeline.get_by_name(f"{self.name}_sink")
            sink.connect("new-sample", self._on_sample, Gst)
            self.pipeline.set_state(Gst.State.PLAYING)
        except Exception as error:
            self.node.get_logger().error(f"{self.name} video pipeline unavailable: {error}")

    def _on_sample(self, sink, Gst):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        caps = sample.get_caps().get_structure(0)
        width, height = caps.get_value("width"), caps.get_value("height")
        buffer = sample.get_buffer()
        success, mapped = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.OK
        try:
            image = Image()
            image.header.stamp = self.node.get_clock().now().to_msg()
            image.header.frame_id = f"dji_{self.name}_camera"
            image.width, image.height, image.encoding, image.step = width, height, "bgr8", width * 3
            image.data = bytes(mapped.data)
            self.publisher.publish(image)
        finally:
            buffer.unmap(mapped)
        return Gst.FlowReturn.OK

    def stop(self):
        if self.pipeline is not None:
            self.pipeline.set_state(self.Gst.State.NULL)


class EdgeBridge(Node):
    def __init__(self):
        super().__init__("dji_edge_bridge")
        defaults = {
            "state_url": "http://127.0.0.1:8088/v1/state", "state_poll_hz": 10.0,
            "primary_relay_port": 5602, "fpv_relay_port": 5612, "rtp_payload_type": 96,
            "rtp_latency_ms": 20, "publish_video": True,
            "primary_topic": "/dji/primary/image_raw", "fpv_topic": "/dji/fpv/image_raw",
            "navigation_topic": "/dji/navigation/state", "flight_topic": "/dji/telemetry/flight",
            "rtk_topic": "/dji/telemetry/rtk", "gimbal_topic": "/dji/telemetry/gimbal", "diagnostics_topic": "/dji/diagnostics",
        }
        for key, value in defaults.items():
            self.declare_parameter(key, value)
        p = lambda key: self.get_parameter(key).value
        self.state_url = p("state_url")
        self.last_sequences = {}
        self.poll_errors = 0
        self.last_poll_error_text = None
        self.last_poll_error_log = 0.0
        self.navigation_pub = self.create_publisher(NavigationState, p("navigation_topic"), 10)
        self.flight_pub = self.create_publisher(String, p("flight_topic"), 10)
        self.rtk_pub = self.create_publisher(String, p("rtk_topic"), 10)
        self.gimbal_pub = self.create_publisher(String, p("gimbal_topic"), 10)
        self.diagnostics_pub = self.create_publisher(DiagnosticArray, p("diagnostics_topic"), 10)
        self.videos = []
        if p("publish_video"):
            self.videos = [
                VideoReceiver(self, "primary", int(p("primary_relay_port")), p("primary_topic"), int(p("rtp_payload_type")), int(p("rtp_latency_ms"))),
                VideoReceiver(self, "fpv", int(p("fpv_relay_port")), p("fpv_topic"), int(p("rtp_payload_type")), int(p("rtp_latency_ms"))),
            ]
        self.create_timer(1.0 / max(1.0, float(p("state_poll_hz"))), self.poll_state)
        self.create_timer(1.0, self.publish_diagnostics)

    def poll_state(self):
        try:
            with urlopen(self.state_url, timeout=0.25) as response:
                state = json.loads(response.read())
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            self.poll_errors += 1
            text = str(error)
            now = time.monotonic()
            if text != self.last_poll_error_text or now - self.last_poll_error_log >= 5.0:
                self.get_logger().warning(f"edge state unavailable: {error}")
                self.last_poll_error_text, self.last_poll_error_log = text, now
            return
        self.last_poll_error_text = None
        self.publish_raw("flight", state.get("flight"), self.flight_pub)
        self.publish_raw("rtk", state.get("rtk"), self.rtk_pub)
        for index, gimbal in enumerate(state.get("gimbals", [])):
            self.publish_raw(f"gimbal:{index}", gimbal, self.gimbal_pub)
        self.publish_navigation(state)

    def publish_raw(self, name, record, publisher):
        if not record or self.last_sequences.get(name) == record.get("sequence"):
            return
        self.last_sequences[name] = record.get("sequence")
        message = String()
        message.data = json.dumps(record, separators=(",", ":"), sort_keys=True)
        publisher.publish(message)

    def publish_navigation(self, state):
        flight, rtk = state.get("flight") or {}, state.get("rtk") or {}
        flight_fields = flight.get("data", {}).get("fields", {})
        rtk_fields = rtk.get("data", {}).get("fields", {})
        rtk_lat, rtk_lon = unwrap(rtk_fields, "fusion.latitude_deg"), unwrap(rtk_fields, "fusion.longitude_deg")
        rtk_valid = bool(unwrap(rtk_fields, "is_being_used", False)) and finite(rtk_lat) and finite(rtk_lon)
        lat, lon = (rtk_lat, rtk_lon) if rtk_valid else (unwrap(flight_fields, "aircraft.latitude_deg"), unwrap(flight_fields, "aircraft.longitude_deg"))
        if not (finite(lat) and finite(lon)):
            return
        msg = NavigationState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "wgs84"
        msg.latitude_deg, msg.longitude_deg = float(lat), float(lon)
        msg.altitude_m = float(unwrap(flight_fields, "aircraft.altitude_m", 0.0) or 0.0)
        msg.heading_deg = float(unwrap(flight_fields, "heading_deg", 0.0) or 0.0)
        msg.velocity_north_m_s = float(unwrap(flight_fields, "velocity.north_m_s", 0.0) or 0.0)
        msg.velocity_east_m_s = float(unwrap(flight_fields, "velocity.east_m_s", 0.0) or 0.0)
        msg.velocity_down_m_s = float(unwrap(flight_fields, "velocity.down_m_s", 0.0) or 0.0)
        msg.position_source = NavigationState.POSITION_RTK if rtk_valid else NavigationState.POSITION_GPS_FALLBACK
        msg.position_valid, msg.rtk_valid = True, rtk_valid
        msg.gps_signal_level = int(unwrap(flight_fields, "gps.signal_level", 0) or 0)
        msg.session = state.get("session") or ""
        source = rtk if rtk_valid else flight
        msg.android_mono_ns = int(source.get("android_mono_ns") or 0)
        msg.edge_receive_mono_ns = int(source.get("edge_receive_mono_ns") or 0)
        age_ns = source.get("network_age_ns")
        msg.transport_age_s = float(age_ns) / 1e9 if isinstance(age_ns, int) else float("nan")
        self.navigation_pub.publish(msg)

    def publish_diagnostics(self):
        array = DiagnosticArray()
        array.header.stamp = self.get_clock().now().to_msg()
        status = DiagnosticStatus(name="dji_edge_bridge", level=DiagnosticStatus.WARN if self.poll_errors else DiagnosticStatus.OK, message="edge state polling")
        status.values = [KeyValue(key="state_url", value=self.state_url), KeyValue(key="poll_errors", value=str(self.poll_errors))]
        array.status = [status]
        self.diagnostics_pub.publish(array)

    def destroy_node(self):
        for video in self.videos:
            video.stop()
        return super().destroy_node()


def main():
    rclpy.init()
    node = EdgeBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
