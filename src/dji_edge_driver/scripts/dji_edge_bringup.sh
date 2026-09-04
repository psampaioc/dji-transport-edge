#!/usr/bin/env bash
set -eo pipefail

workspace=/workspace
source /opt/ros/humble/setup.bash
cd "$workspace"
if [ ! -f "$workspace/install/setup.bash" ]; then
  echo "Workspace is not built. Run: colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release" >&2
  exit 2
fi
source "$workspace/install/setup.bash"
exec ros2 launch dji_edge_driver dji_edge_bringup.launch.py "$@"
