from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1]


def test_mapper_installs_only_public_configuration_assets():
    cmake = (PACKAGE_ROOT / "CMakeLists.txt").read_text(encoding="utf-8")

    assert "install(DIRECTORY config/" not in cmake
    assert "config/mapper.yaml" in cmake
    assert "config/mapper.local.yaml.example" in cmake


def test_bringup_prefers_the_ignored_workspace_local_mapper_config():
    bringup = (
        PACKAGE_ROOT.parents[0]
        / "dji_edge_driver"
        / "launch"
        / "dji_edge_bringup.launch.py"
    ).read_text(encoding="utf-8")

    assert "/workspace/src/dji_edge_mapper/config/mapper.local.yaml" in bringup


def test_local_relative_map_assets_fall_back_to_the_workspace_source_tree():
    publisher = (PACKAGE_ROOT / "src" / "map_publisher.cpp").read_text(encoding="utf-8")
    localizer = (PACKAGE_ROOT / "scripts" / "drone_localization_node.py").read_text(encoding="utf-8")

    assert "/workspace/src/dji_edge_mapper" in publisher
    assert "/workspace/src/dji_edge_mapper" in localizer


def test_localizer_never_uses_ros_delivery_header_as_a_timing_fallback():
    localizer = (PACKAGE_ROOT / "scripts" / "drone_localization_node.py").read_text(encoding="utf-8")

    assert "header_stamp_ns" not in localizer
    assert "stamp_ns = message.edge_receive_mono_ns" in localizer
