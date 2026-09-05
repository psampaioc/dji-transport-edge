from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = Path(get_package_share_directory("dji_edge_mapper"))
    default_config = str(share / "config" / "mapper.yaml")
    config = LaunchConfiguration("config_file")
    runtime_config = LaunchConfiguration("runtime_config_file")
    return LaunchDescription([
        DeclareLaunchArgument("config_file", default_value=default_config),
        DeclareLaunchArgument("runtime_config_file", default_value=""),
        OpaqueFunction(function=lambda context: mapper_nodes(context, config, runtime_config)),
    ])


def mapper_nodes(context, config, runtime_config):
    parameters = [config.perform(context)]
    runtime_path = runtime_config.perform(context)
    if runtime_path and Path(runtime_path).is_file():
        parameters.append(runtime_path)
    return [
        Node(package="dji_edge_mapper", executable="map_publisher", output="screen", parameters=parameters),
        Node(package="dji_edge_mapper", executable="drone_localization_node.py", output="screen", parameters=parameters),
    ]
