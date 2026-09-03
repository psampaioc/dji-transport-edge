from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    driver_share = Path(get_package_share_directory("dji_edge_driver"))
    mapper_share = Path(get_package_share_directory("dji_edge_mapper"))
    driver_config = LaunchConfiguration("driver_config_file")
    mapper_config = LaunchConfiguration("mapper_config_file")
    return LaunchDescription([
        DeclareLaunchArgument("driver_config_file", default_value=str(driver_share / "config" / "bridge.yaml")),
        DeclareLaunchArgument("mapper_config_file", default_value=str(mapper_share / "config" / "mapper.yaml")),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(driver_share / "launch" / "dji_edge_driver.launch.py")),
            launch_arguments={"config_file": driver_config}.items(),
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(mapper_share / "launch" / "dji_edge_mapper.launch.py")),
            launch_arguments={"config_file": mapper_config}.items(),
        ),
    ])
