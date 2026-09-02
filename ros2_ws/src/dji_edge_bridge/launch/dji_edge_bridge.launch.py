from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_config = str(Path(get_package_share_directory("dji_edge_bridge")) / "config" / "bridge.yaml")
    config = LaunchConfiguration("config_file")
    return LaunchDescription([
        DeclareLaunchArgument("config_file", default_value=default_config),
        Node(package="dji_edge_bridge", executable="dji_edge_bridge", name="dji_edge_bridge", output="screen", parameters=[config]),
    ])
