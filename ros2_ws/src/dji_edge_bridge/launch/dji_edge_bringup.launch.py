from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    bridge_share = Path(get_package_share_directory("dji_edge_bridge"))
    mapper_share = Path(get_package_share_directory("dji_edge_mapper"))
    bridge_config = LaunchConfiguration("bridge_config_file")
    mapper_config = LaunchConfiguration("mapper_config_file")
    return LaunchDescription([
        DeclareLaunchArgument("bridge_config_file", default_value=str(bridge_share / "config" / "bridge.yaml")),
        DeclareLaunchArgument("mapper_config_file", default_value=str(mapper_share / "config" / "mapper.yaml")),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(bridge_share / "launch" / "dji_edge_bridge.launch.py")),
            launch_arguments={"config_file": bridge_config}.items(),
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(mapper_share / "launch" / "dji_edge_mapper.launch.py")),
            launch_arguments={"config_file": mapper_config}.items(),
        ),
    ])
