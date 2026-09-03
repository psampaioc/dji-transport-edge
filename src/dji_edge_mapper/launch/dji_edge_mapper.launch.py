from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = Path(get_package_share_directory("dji_edge_mapper"))
    default_config = str(share / "config" / "mapper.yaml")
    config = LaunchConfiguration("config_file")
    return LaunchDescription([
        DeclareLaunchArgument("config_file", default_value=default_config),
        Node(package="dji_edge_mapper", executable="map_publisher", output="screen", parameters=[config]),
        Node(package="dji_edge_mapper", executable="drone_localization_node.py", output="screen", parameters=[config]),
    ])
