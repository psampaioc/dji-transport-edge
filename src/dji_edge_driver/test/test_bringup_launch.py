from pathlib import Path
import re


def test_complete_bringup_declares_opt_in_rviz_with_installed_config():
    path = Path(__file__).parents[1] / "launch" / "dji_edge_bringup.launch.py"
    source = path.read_text(encoding="utf-8")
    assert 'DeclareLaunchArgument("rviz", default_value="true")' in source
    assert 'DeclareLaunchArgument("preview_windows", default_value="false")' in source
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
    driver = (package / "src" / "dji_edge_driver_node.py").read_text(encoding="utf-8")
    frame_context = driver[driver.index("    def frame_context("):driver.index("    def record_frame_context(")]
    assigned = set(re.findall(r"message\.([a-zA-Z_][a-zA-Z0-9_]*)\s*=", frame_context))
    assert assigned <= message_fields


def test_video_pipeline_uses_rtp_timestamp_jitter_mode_for_frame_association():
    package = Path(__file__).parents[1]
    pipeline = (package / "src" / "dji_edge_transport_core" / "feed_pipeline.py").read_text(encoding="utf-8")
    assert "rtpjitterbuffer" in pipeline
    assert "mode=none" in pipeline


def test_default_video_pipeline_is_headless_and_has_one_appsink_branch():
    package = Path(__file__).parents[1]
    pipeline = (package / "src" / "dji_edge_transport_core" / "feed_pipeline.py").read_text(encoding="utf-8")
    config = (package / "config" / "bridge.yaml").read_text(encoding="utf-8")
    assert "preview_windows: false" in config
    assert 'if not self.preview_windows:' in pipeline
    assert "return source_to_bgr + appsink" in pipeline
    assert "fakesink" not in pipeline


def test_disabling_ros_image_publication_does_not_disable_rtp_ingress_pipelines():
    package = Path(__file__).parents[1]
    driver = (package / "src" / "dji_edge_driver_node.py").read_text(encoding="utf-8")
    create_videos = driver[driver.index("    def _create_videos(self):"):driver.index("    def _create_dashboard(self):")]
    assert 'if not self.parameter("publish_video")' not in create_videos
    assert "ros_publish_enabled = self.parameter(\"publish_video\")" in create_videos


def test_transport_evidence_records_the_decoder_selected_for_each_feed():
    package = Path(__file__).parents[1]
    driver = (package / "src" / "dji_edge_driver_node.py").read_text(encoding="utf-8")
    assert '"decoder": feed["decoder"]' in driver


def test_rviz_primary_image_is_opt_in_to_avoid_default_bgr_ros_copy():
    package = Path(__file__).parents[2] / "dji_edge_mapper"
    rviz = (package / "rviz" / "dji_edge_mapper.rviz").read_text(encoding="utf-8")
    image = rviz[rviz.index("Class: rviz_default_plugins/Image"):]
    assert "Enabled: false" in image
    assert "Value: false" in image
