from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    driver_share = Path(get_package_share_directory("dji_edge_driver"))
    mapper_share = Path(get_package_share_directory("dji_edge_mapper"))
    driver_config = LaunchConfiguration("driver_config_file")
    mapper_config = LaunchConfiguration("mapper_config_file")
    mapper_runtime_config = LaunchConfiguration("mapper_runtime_config_file")
    rviz = LaunchConfiguration("rviz")
    preview_windows = LaunchConfiguration("preview_windows")
    local_mapper_config = Path("/workspace/src/dji_edge_mapper/config/mapper.local.yaml")
    local_driver_config = Path("/workspace/bridge.local.yaml")
    local_mapper_runtime_config = Path("/workspace/mapper.runtime.local.yaml")
    default_mapper_config = local_mapper_config if local_mapper_config.exists() else mapper_share / "config" / "mapper.yaml"
    default_driver_config = local_driver_config if local_driver_config.exists() else driver_share / "config" / "bridge.yaml"
    return LaunchDescription([
        DeclareLaunchArgument("driver_config_file", default_value=str(default_driver_config)),
        DeclareLaunchArgument("mapper_config_file", default_value=str(default_mapper_config)),
        DeclareLaunchArgument("mapper_runtime_config_file", default_value=str(local_mapper_runtime_config)),
        DeclareLaunchArgument("rviz", default_value="true"),
        DeclareLaunchArgument("preview_windows", default_value="false"),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(driver_share / "launch" / "dji_edge_driver.launch.py")),
            launch_arguments={"config_file": driver_config, "preview_windows": preview_windows}.items(),
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(mapper_share / "launch" / "dji_edge_mapper.launch.py")),
            launch_arguments={"config_file": mapper_config, "runtime_config_file": mapper_runtime_config}.items(),
        ),
        Node(
            package="rviz2", executable="rviz2", output="screen",
            arguments=["-d", str(mapper_share / "rviz" / "dji_edge_mapper.rviz")],
            condition=IfCondition(rviz),
        ),
    ])
