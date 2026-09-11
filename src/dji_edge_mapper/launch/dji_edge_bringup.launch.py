from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    transport_share = Path(get_package_share_directory("dji_edge_transport"))
    mapper_share = Path(get_package_share_directory("dji_edge_mapper"))
    transport_config = LaunchConfiguration("transport_config")
    mapper_config = LaunchConfiguration("mapper_config")
    runtime_config = LaunchConfiguration("mapper_runtime_config")
    return LaunchDescription([
        DeclareLaunchArgument(
            "transport_config", default_value=str(transport_share / "config" / "transport.yaml")),
        DeclareLaunchArgument(
            "mapper_config", default_value=str(mapper_share / "config" / "mapper.yaml")),
        DeclareLaunchArgument("mapper_runtime_config", default_value=""),
        DeclareLaunchArgument("rviz", default_value="true"),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(transport_share / "launch" / "transport.launch.py")),
            launch_arguments={"config_file": transport_config}.items()),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(mapper_share / "launch" / "dji_edge_mapper.launch.py")),
            launch_arguments={"config_file": mapper_config, "runtime_config_file": runtime_config}.items()),
        Node(
            condition=IfCondition(LaunchConfiguration("rviz")),
            package="rviz2", executable="rviz2", output="screen",
            arguments=["-d", str(mapper_share / "rviz" / "dji_edge_mapper.rviz")]),
    ])
