from pathlib import Path
import re


def test_complete_bringup_declares_opt_in_rviz_with_installed_config():
    path = Path(__file__).parents[1] / "launch" / "dji_edge_bringup.launch.py"
    source = path.read_text(encoding="utf-8")
    assert 'DeclareLaunchArgument("rviz", default_value="true")' in source
    assert 'DeclareLaunchArgument("preview_windows", default_value="true")' in source
    assert 'DeclareLaunchArgument("mapper_runtime_config_file"' in source
    assert '"runtime_config_file": mapper_runtime_config' in source
    assert 'package="rviz2", executable="rviz2"' in source
    assert "dji_edge_mapper.rviz" in source


def test_frame_context_publisher_assigns_only_declared_message_fields():
    package = Path(__file__).parents[1]
    message_fields = {
        line.split()[1]
        for line in (package / "msg" / "FrameContext.msg").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" not in line
    }
    driver = (package / "scripts" / "dji_edge_driver_node.py").read_text(encoding="utf-8")
    frame_context = driver[driver.index("    def frame_context("):driver.index("    def record_frame_context(")]
    assigned = set(re.findall(r"message\.([a-zA-Z_][a-zA-Z0-9_]*)\s*=", frame_context))
    assert assigned <= message_fields


def test_video_pipeline_uses_rtp_timestamp_jitter_mode_for_frame_association():
    package = Path(__file__).parents[1]
    driver = (package / "scripts" / "dji_edge_driver_node.py").read_text(encoding="utf-8")
    pipeline = driver[driver.index("    def _description("):driver.index("    def _on_sample(")]
    assert "rtpjitterbuffer" in pipeline
    assert "mode=none" in pipeline
