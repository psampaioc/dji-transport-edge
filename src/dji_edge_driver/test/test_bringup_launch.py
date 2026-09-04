from pathlib import Path


def test_complete_bringup_declares_opt_in_rviz_with_installed_config():
    path = Path(__file__).parents[1] / "launch" / "dji_edge_bringup.launch.py"
    source = path.read_text(encoding="utf-8")
    assert 'DeclareLaunchArgument("rviz", default_value="true")' in source
    assert 'DeclareLaunchArgument("preview_windows", default_value="true")' in source
    assert 'package="rviz2", executable="rviz2"' in source
    assert "dji_edge_mapper.rviz" in source
