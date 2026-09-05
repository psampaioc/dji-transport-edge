import os
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).parents[1] / "scripts" / "dji_edge_bringup.sh"


def test_supervisor_source_is_executable_and_install_name_is_stable():
    cmake = (Path(__file__).parents[1] / "CMakeLists.txt").read_text(encoding="utf-8")

    assert os.access(SCRIPT, os.X_OK)
    assert "RENAME dji_edge_bringup" in cmake


def run_supervisor(tmp_path, *, marker=False, child_exit=0):
    workspace = tmp_path / "workspace"
    (workspace / "install").mkdir(parents=True)
    (workspace / "install" / "setup.bash").write_text("", encoding="utf-8")
    marker_path = workspace / ".runtime" / "dji-edge-restart.request"
    if marker:
        marker_path.parent.mkdir()
        marker_path.write_text("restart\n", encoding="utf-8")

    calls = tmp_path / "calls.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ros = fake_bin / "ros2"
    fake_ros.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_ROS_CALLS\"\n"
        "exit \"$FAKE_ROS_EXIT\"\n",
        encoding="utf-8",
    )
    fake_ros.chmod(0o755)

    environment = os.environ | {
        "DJI_EDGE_WORKSPACE": str(workspace),
        "FAKE_ROS_CALLS": str(calls),
        "FAKE_ROS_EXIT": str(child_exit),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    result = subprocess.run(["bash", str(SCRIPT), "rviz:=false"], env=environment, text=True, capture_output=True, check=False)
    invocations = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, invocations, marker_path


def test_normal_exit_launches_once_without_restart_marker(tmp_path):
    result, invocations, marker = run_supervisor(tmp_path)
    assert result.returncode == 0
    assert invocations == ["launch dji_edge_driver dji_edge_bringup.launch.py rviz:=false"]
    assert not marker.exists()


def test_requested_restart_is_consumed_once_then_relaunched(tmp_path):
    result, invocations, marker = run_supervisor(tmp_path, marker=True)
    assert result.returncode == 0
    assert len(invocations) == 2
    assert not marker.exists()


def test_child_failure_without_request_preserves_exit_code(tmp_path):
    result, invocations, marker = run_supervisor(tmp_path, child_exit=7)
    assert result.returncode == 7
    assert len(invocations) == 1
    assert not marker.exists()
