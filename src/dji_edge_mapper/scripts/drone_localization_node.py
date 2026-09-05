#!/usr/bin/env python3
"""Projects the normalized DJI navigation message into the already-local map frame."""

import json
import math
from collections import Counter
from pathlib import Path

import pyproj
import rclpy
from ament_index_python.packages import get_package_share_directory
from dji_edge_driver.msg import FrameContext, NavigationState
from geometry_msgs.msg import PoseStamped, TransformStamped
from nav_msgs.msg import Path as PathMessage
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from tf2_ros import TransformBroadcaster


def reliable_qos(transient=False):
    qos = QoSProfile(depth=1)
    qos.reliability = ReliabilityPolicy.RELIABLE
    if transient:
        qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
    return qos


def best_effort_qos():
    qos = QoSProfile(depth=1)
    qos.reliability = ReliabilityPolicy.BEST_EFFORT
    return qos


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def compass_to_ros_yaw(heading_deg):
    angle = math.radians(90.0 - heading_deg)
    return math.atan2(math.sin(angle), math.cos(angle))


def resolve_mapper_asset(configured):
    path = Path(configured)
    if path.is_absolute():
        return path
    package_path = Path(get_package_share_directory("dji_edge_mapper")) / path
    if package_path.exists():
        return package_path
    workspace_path = Path("/workspace/src/dji_edge_mapper") / path
    return workspace_path if workspace_path.exists() else package_path


class DroneLocalizationNode(Node):
    def __init__(self):
        super().__init__("drone_localization_node")
        for name, default in {
            "enabled": False,
            "navigation_topic": "/dji/navigation/state",
            "frame_context_topic": "/dji/primary/frame_context",
            "pose_topic": "/dji/navigation/pose",
            "frame_pose_topic": "/dji/frame/pose",
            "path_topic": "/dji/navigation/path",
            "status_topic": "/dji/navigation/status",
            "frame_id": "map",
            "child_frame": "dji_aircraft",
            "utm_zone": 29,
            "utm_south": False,
            "map_metadata_path": "config/map_metadata.json",
            "enable_map_bounds_check": True,
            "map_margin_m": 5.0,
            "enable_jump_gate": True,
            "max_speed_mps": 20.0,
            "max_gate_dt_s": 10.0,
            "position_tolerance_m": 2.0,
            "min_path_spacing_m": 0.15,
            "max_history_points": 5000,
        }.items():
            self.declare_parameter(name, default)

        p = lambda name: self.get_parameter(name).value
        if not bool(p("enabled")):
            self.get_logger().warning("Localization disabled: create config/mapper.local.yaml and provide a site map.")
            return
        self.frame_id, self.child_frame = p("frame_id"), p("child_frame")
        self.map_margin_m = float(p("map_margin_m"))
        self.bounds_check, self.jump_gate = bool(p("enable_map_bounds_check")), bool(p("enable_jump_gate"))
        self.max_speed_mps = float(p("max_speed_mps"))
        self.max_gate_dt_s = float(p("max_gate_dt_s"))
        self.tolerance = float(p("position_tolerance_m"))
        self.min_path_spacing_m = max(0.0, float(p("min_path_spacing_m")))
        self.max_history = max(0, int(p("max_history_points")))
        zone, south = int(p("utm_zone")), bool(p("utm_south"))
        if not 1 <= zone <= 60:
            raise ValueError(f"utm_zone must be in [1, 60], got {zone}")
        self.projector = pyproj.Transformer.from_crs("EPSG:4326", f"EPSG:{(32700 if south else 32600) + zone}", always_xy=True)
        self.translation, self.minimum, self.maximum = self._load_map_metadata(str(p("map_metadata_path")))
        self.path = PathMessage()
        self.path.header.frame_id = self.frame_id
        self.last_point = None
        self.last_stamp_ns = None
        self.counts = Counter()
        self.tf = TransformBroadcaster(self)
        self.pose_pub = self.create_publisher(PoseStamped, p("pose_topic"), reliable_qos())
        self.frame_pose_pub = self.create_publisher(PoseStamped, p("frame_pose_topic"), reliable_qos())
        self.path_pub = self.create_publisher(PathMessage, p("path_topic"), reliable_qos(transient=True))
        self.status_pub = self.create_publisher(String, p("status_topic"), reliable_qos(transient=True))
        self.create_subscription(NavigationState, p("navigation_topic"), self.on_navigation, reliable_qos())
        self.create_subscription(FrameContext, p("frame_context_topic"), self.on_frame_context, best_effort_qos())
        self.create_timer(1.0, self.publish_status)
        self.get_logger().info(
            f"Localization online: {p('navigation_topic')} -> pose={p('pose_topic')} path={p('path_topic')} frame={self.frame_id}"
        )

    def _project_position(self, latitude_deg, longitude_deg, altitude_m):
        if not all(finite(value) for value in (latitude_deg, longitude_deg, altitude_m)):
            return None
        if not -90.0 <= latitude_deg <= 90.0 or not -180.0 <= longitude_deg <= 180.0:
            return None
        try:
            easting, northing = self.projector.transform(longitude_deg, latitude_deg)
        except pyproj.exceptions.ProjError as error:
            self.get_logger().warning(f"Coordinate projection failed: {error}")
            return None
        if not finite(easting) or not finite(northing):
            return None
        point = (easting - self.translation[0], northing - self.translation[1], altitude_m - self.translation[2])
        if self.bounds_check and not all(
            low - self.map_margin_m <= value <= high + self.map_margin_m
            for value, low, high in zip(point, self.minimum, self.maximum)
        ):
            return None
        return point

    def _load_map_metadata(self, configured):
        path = resolve_mapper_asset(configured)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            values = tuple(
                tuple(float(value) for value in payload[key])
                for key in ("applied_translation", "bbox_min_after", "bbox_max_after")
            )
        except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Cannot load valid map metadata from {path}: {error}") from error
        if any(len(value) != 3 or not all(finite(item) for item in value) for value in values):
            raise RuntimeError(f"Map metadata must contain three finite XYZ values: {path}")
        return values

    def on_navigation(self, message):
        self.counts["received"] += 1
        if not message.position_valid or not all(
            finite(value) for value in (message.latitude_deg, message.longitude_deg, message.altitude_m, message.heading_deg)
        ):
            self.counts["rejected_invalid"] += 1
            return
        if not -90.0 <= message.latitude_deg <= 90.0 or not -180.0 <= message.longitude_deg <= 180.0:
            self.counts["rejected_invalid"] += 1
            return
        point = self._project_position(message.latitude_deg, message.longitude_deg, message.altitude_m)
        if point is None:
            self.counts["rejected_projection"] += 1
            return
        # Edge receive monotonic time is only a local jump-gate diagnostic. ROS
        # delivery headers never substitute for Android/DJI source timing.
        stamp_ns = message.edge_receive_mono_ns
        if self.jump_gate and stamp_ns and self.last_point is not None and self.last_stamp_ns is not None:
            dt = max(0.0, (stamp_ns - self.last_stamp_ns) / 1e9)
            distance = math.dist(point, self.last_point)
            if dt == 0.0 or distance > self.tolerance + self.max_speed_mps * min(dt, self.max_gate_dt_s):
                self.counts["rejected_jump"] += 1
                return
        pose = PoseStamped()
        pose.header, pose.header.frame_id = message.header, self.frame_id
        pose.pose.position.x, pose.pose.position.y, pose.pose.position.z = point
        yaw = compass_to_ros_yaw(message.heading_deg)
        pose.pose.orientation.z, pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        self.pose_pub.publish(pose)
        transform = TransformStamped()
        transform.header, transform.child_frame_id = pose.header, self.child_frame
        transform.transform.translation.x = pose.pose.position.x
        transform.transform.translation.y = pose.pose.position.y
        transform.transform.translation.z = pose.pose.position.z
        transform.transform.rotation = pose.pose.orientation
        self.tf.sendTransform(transform)
        if not self.path.poses or math.dist(
            point,
            (
                self.path.poses[-1].pose.position.x,
                self.path.poses[-1].pose.position.y,
                self.path.poses[-1].pose.position.z,
            ),
        ) >= self.min_path_spacing_m:
            self.path.poses.append(pose)
            if self.max_history and len(self.path.poses) > self.max_history:
                self.path.poses.pop(0)
            self.path.header.stamp = pose.header.stamp
            self.path_pub.publish(self.path)
        self.last_point, self.last_stamp_ns = point, stamp_ns or None
        self.counts["accepted"] += 1
        self.counts["accepted_rtk" if message.rtk_valid else "accepted_gps_fallback"] += 1

    def on_frame_context(self, message):
        """Publish only a pose proven to belong to this displayed ROS image."""
        self.counts["frame_context_received"] += 1
        if message.association_quality == FrameContext.ASSOCIATION_UNAVAILABLE or not message.position_valid:
            self.counts["frame_context_unavailable"] += 1
            return
        point = self._project_position(message.latitude_deg, message.longitude_deg, message.altitude_m)
        if point is None:
            self.counts["frame_context_rejected"] += 1
            return
        pose = PoseStamped()
        pose.header, pose.header.frame_id = message.header, self.frame_id
        pose.pose.position.x, pose.pose.position.y, pose.pose.position.z = point
        yaw = compass_to_ros_yaw(message.heading_deg)
        pose.pose.orientation.z, pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        self.frame_pose_pub.publish(pose)
        self.counts["frame_context_published"] += 1

    def publish_status(self):
        msg = String()
        msg.data = json.dumps(
            {
                "accepted": self.counts["accepted"],
                "accepted_gps_fallback": self.counts["accepted_gps_fallback"],
                "accepted_rtk": self.counts["accepted_rtk"],
                "frame_context_published": self.counts["frame_context_published"],
                "frame_context_received": self.counts["frame_context_received"],
                "frame_context_rejected": self.counts["frame_context_rejected"],
                "frame_context_unavailable": self.counts["frame_context_unavailable"],
                "path_poses": len(self.path.poses),
                "received": self.counts["received"],
                "rejected": self.counts["received"] - self.counts["accepted"],
                "rejected_invalid": self.counts["rejected_invalid"],
                "rejected_jump": self.counts["rejected_jump"],
                "rejected_outside_map": self.counts["rejected_outside_map"],
                "rejected_projection": self.counts["rejected_projection"],
            },
            sort_keys=True,
        )
        self.status_pub.publish(msg)


def main():
    rclpy.init()
    node = DroneLocalizationNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        try:
            node.destroy_node()
        except KeyboardInterrupt:
            pass
        try:
            if rclpy.ok():
                rclpy.shutdown()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
