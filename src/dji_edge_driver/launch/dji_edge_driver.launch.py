from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_config = str(Path(get_package_share_directory("dji_edge_driver")) / "config" / "bridge.yaml")
    config = LaunchConfiguration("config_file")
    preview_windows = LaunchConfiguration("preview_windows")
    return LaunchDescription([
        DeclareLaunchArgument("config_file", default_value=default_config),
        DeclareLaunchArgument("preview_windows", default_value="true"),
        Node(package="dji_edge_driver", executable="dji_edge_driver", name="dji_edge_driver", output="screen", parameters=[config, {"preview_windows": preview_windows}]),
    ])
