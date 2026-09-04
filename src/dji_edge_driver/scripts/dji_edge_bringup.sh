#!/usr/bin/env bash
set -o pipefail

workspace=/workspace
source /opt/ros/humble/setup.bash
cd "$workspace"
if [ ! -f "$workspace/install/setup.bash" ]; then
  echo "Workspace is not built. Run: colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release" >&2
  exit 2
fi
source "$workspace/install/setup.bash"
restart_marker="$workspace/.runtime/dji-edge-restart.request"
while true; do
  if ros2 launch dji_edge_driver dji_edge_bringup.launch.py "$@"; then
    exit_code=0
  else
    exit_code=$?
  fi
  if [ -f "$restart_marker" ]; then
    rm -f "$restart_marker"
    echo "Dashboard requested restart; relaunching managed Edge stack."
    continue
  fi
  exit "$exit_code"
done
