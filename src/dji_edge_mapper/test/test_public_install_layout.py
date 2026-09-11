from pathlib import Path
import importlib.util


PACKAGE_ROOT = Path(__file__).parents[1]


def test_mapper_installs_only_public_configuration_assets():
    cmake = (PACKAGE_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

    assert "install(DIRECTORY config/" not in cmake
    assert "config/mapper.yaml" in cmake
    assert "config/mapper.local.yaml.example" in cmake


def test_new_bringup_uses_transport_and_never_references_legacy_driver():
    bringup = (
        PACKAGE_ROOT
        / "launch"
        / "dji_edge_bringup.launch.py"
    ).read_text(encoding="utf-8")

    assert 'get_package_share_directory("dji_edge_transport")' in bringup
    assert "dji_edge_driver" not in bringup


def test_local_relative_map_assets_fall_back_to_the_workspace_source_tree():
    publisher = (PACKAGE_ROOT / "src" / "map_publisher.cpp").read_text(encoding="utf-8")
    localizer = (PACKAGE_ROOT / "scripts" / "drone_localization_node.py").read_text(encoding="utf-8")

    assert "/workspace/src/dji_edge_mapper" in publisher
    assert "/workspace/src/dji_edge_mapper" in localizer


def test_localizer_never_uses_ros_delivery_header_as_a_timing_fallback():
    localizer = (PACKAGE_ROOT / "scripts" / "drone_localization_node.py").read_text(encoding="utf-8")

    assert "header_stamp_ns" not in localizer
    assert "stamp_ns = message.edge_receive_mono_ns" in localizer


def test_rviz_uses_a_capped_repaint_rate_while_preserving_operator_displays():
    rviz = (PACKAGE_ROOT / "rviz" / "dji_edge_mapper.rviz").read_text(encoding="utf-8")

    assert "Frame Rate: 15" in rviz
    assert "Value: /map/cloud" in rviz
    assert "Value: /dji/navigation/path" in rviz
    assert "Value: /dji/primary/image_raw" in rviz


def test_mapper_runtime_overlay_is_last_and_only_used_when_it_exists(tmp_path, monkeypatch):
    launch_path = PACKAGE_ROOT / "launch" / "dji_edge_mapper.launch.py"
    spec = importlib.util.spec_from_file_location("dji_edge_mapper_launch", launch_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Value:
        def __init__(self, value):
            self.value = value

        def perform(self, _context):
            return self.value

    captured = []
    monkeypatch.setattr(module, "Node", lambda **kwargs: captured.append(kwargs) or kwargs)
    base = str(tmp_path / "mapper.local.yaml")
    runtime = tmp_path / "mapper.runtime.local.yaml"

    module.mapper_nodes(None, Value(base), Value(str(runtime)))
    assert [node["parameters"] for node in captured] == [[base], [base]]

    runtime.write_text("/drone_localization_node:\n", encoding="utf-8")
    captured.clear()
    module.mapper_nodes(None, Value(base), Value(str(runtime)))
    assert [node["parameters"] for node in captured] == [[base, str(runtime)], [base, str(runtime)]]
