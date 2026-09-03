#!/usr/bin/env bash
set -eo pipefail

workspace=/workspace
source /opt/ros/humble/setup.bash
cd "$workspace"
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
source "$workspace/install/setup.bash"
exec ros2 launch dji_edge_driver dji_edge_bringup.launch.py "$@"
