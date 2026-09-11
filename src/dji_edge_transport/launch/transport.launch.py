from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_config = str(
        Path(get_package_share_directory("dji_edge_transport"))
        / "config"
        / "transport.yaml"
    )
    return LaunchDescription([
        DeclareLaunchArgument("config_file", default_value=default_config),
        Node(
            package="dji_edge_transport",
            executable="dji_edge_transport_node",
            name="dji_edge_transport",
            output="screen",
            parameters=[LaunchConfiguration("config_file")],
        ),
    ])
