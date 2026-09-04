import json
import struct
from urllib.request import Request, urlopen

import pytest

from dji_edge_transport_core.clock import ClockMapper
from dji_edge_transport_core.dashboard import DashboardServer
from dji_edge_transport_core.evidence import EvidenceWriter, create_session_directory
from dji_edge_transport_core.navigation import build_navigation
from dji_edge_transport_core.protocol import ProtocolError, decode_json_packet, parse_rtp_packet, video_au_identity
from dji_edge_transport_core.rtp import RawRtpCapture, RtpMetrics
from dji_edge_transport_core.state import LatestState, SequenceTracker
from dji_edge_transport_core.video import LatestFrameBuffer


def test_golden_control_packets_decode_with_stable_envelopes():
    # Contract values mirrored from deploy/protocol/v1/golden_packets.json so
    # this ROS package stays testable when the workspace alone is mounted in Docker.
    packets = [
        {"v": 1, "type": "flight", "session": "fixture-session-0001", "seq": 42, "rx_mono_ns": 1234567890123, "lat": 38.7223},
        {"v": 1, "type": "rtk", "session": "fixture-session-0001", "seq": 11, "rx_mono_ns": 1234567890220, "lat": 38.7223001},
        {"v": 1, "type": "gimbal", "session": "fixture-session-0001", "seq": 77, "rx_mono_ns": 1234567890330, "pitch_deg": -45.25},
        {"v": 1, "type": "battery", "session": "fixture-session-0001", "seq": 5, "rx_mono_ns": 1234567890380, "percent": 93},
        {"v": 1, "type": "health", "session": "fixture-session-0001", "seq": 3, "rx_mono_ns": 1234567890440, "sender_drops": 0},
        {"v": 1, "type": "video_au", "session": "fixture-session-0001", "feed": "primary", "source": "FPV_CAM", "frame_seq": 9321, "rtp_ssrc": 305419896, "rtp_ts": 987654321, "au_first_byte_rx_mono_ns": 1234567890500, "au_complete_rx_mono_ns": 1234567891987},
    ]
    decoded = [decode_json_packet(json.dumps(packet).encode(), 1) for packet in packets]
    assert [packet.packet_type for packet in decoded] == ["flight", "rtk", "gimbal", "battery", "health", "video_au"]
    assert decoded[-1].stream == "primary"
    assert decoded[-1].body["rtp_ssrc"] == 305419896


def test_video_au_identity_keeps_android_time_and_optional_dji_time_separate():
    packet = decode_json_packet(json.dumps({
        "v": 1, "type": "video_au", "session": "s", "feed": "primary", "frame_seq": 9,
        "rtp_ssrc": 12, "rtp_ts": 34, "au_first_byte_rx_mono_ns": 100,
        "au_complete_rx_mono_ns": 125, "dji_source_timestamp_ns": 77,
        "dji_timestamp_source": "dji_callback_documented_clock",
    }).encode(), 1)
    identity = video_au_identity(packet)
    assert identity.android_first_byte_mono_ns == 100
    assert identity.android_complete_mono_ns == 125
    assert identity.dji_source_timestamp_ns == 77
    assert identity.dji_timestamp_source == "dji_callback_documented_clock"


@pytest.mark.parametrize("packet", [
    {"v": 1, "type": "video_au", "session": "s", "feed": "other", "frame_seq": 1, "rtp_ssrc": 1, "rtp_ts": 2, "au_first_byte_rx_mono_ns": 3},
    {"v": 1, "type": "video_au", "session": "s", "feed": "primary", "frame_seq": 1, "rtp_ssrc": 1, "rtp_ts": 2, "au_first_byte_rx_mono_ns": 3, "dji_timestamp_source": "orphan"},
])
def test_video_au_identity_rejects_invalid_contract(packet):
    with pytest.raises(ProtocolError):
        decoded = decode_json_packet(json.dumps(packet).encode(), 1)
        video_au_identity(decoded)


def test_complete_fragment_replaces_no_partial_state_and_duplicate_is_ignored():
    mapper, state, tracker = ClockMapper(), LatestState(ClockMapper()), SequenceTracker()
    base = {"v": 1, "type": "rtk", "session": "s", "stream": "rtk:0", "rx_mono_ns": 100}
    first = decode_json_packet(json.dumps({**base, "seq": 1, "data": {"sample_sequence": 8, "chunk_index": 0, "chunk_count": 2, "fields": {"fusion.latitude_deg": {"value": 38.0}}}}).encode(), 1)
    second = decode_json_packet(json.dumps({**base, "seq": 2, "data": {"sample_sequence": 8, "chunk_index": 1, "chunk_count": 2, "fields": {"is_being_used": {"value": True}}}}).encode(), 1)
    assert not state.update_packet(first, 1000, ("127.0.0.1", 5500), tracker.observe(("s", "rtk"), 1))
    assert state.snapshot()["rtk"] is None
    assert state.update_packet(second, 1001, ("127.0.0.1", 5500), tracker.observe(("s", "rtk"), 2))
    duplicate = decode_json_packet(json.dumps({**base, "seq": 2, "data": {"sample_sequence": 9, "chunk_index": 0, "chunk_count": 1, "fields": {"fusion.latitude_deg": {"value": 99.0}}}}).encode(), 1)
    assert not state.update_packet(duplicate, 1002, ("127.0.0.1", 5500), tracker.observe(("s", "rtk"), 2))
    assert state.snapshot()["rtk"]["data"]["fields"]["fusion.latitude_deg"]["value"] == 38.0


def test_clock_and_rtp_contract_reject_invalid_data():
    mapper = ClockMapper()
    mapper.add_exchange(10_000, 11_100, 11_200, 10_300)
    assert mapper.android_to_edge(21_000) == 20_000
    rtp = bytes([0x80, 0xE0, 0, 7, 0, 0, 35, 40, 0x12, 0x34, 0x56, 0x78]) + b"payload"
    assert parse_rtp_packet(rtp, 96).marker
    with pytest.raises(ProtocolError):
        parse_rtp_packet(rtp, 97)


def test_evidence_writer_drains_ndjson_and_reports_write_health(tmp_path):
    writer = EvidenceWriter(tmp_path, max_records=2)
    writer.write("telemetry", {"sequence": 1, "kind": "flight"})
    writer.write("telemetry", {"sequence": 2, "kind": "rtk"})
    writer.close()

    records = [json.loads(line) for line in (tmp_path / "telemetry.ndjson").read_text().splitlines()]
    assert records == [{"kind": "flight", "sequence": 1}, {"kind": "rtk", "sequence": 2}]
    assert writer.health()["queue_depth"] == 0
    assert writer.health()["writer_alive"] is False
    assert writer.health()["write_errors"] == 0


def test_evidence_sessions_are_distinct_and_stay_under_configured_root(tmp_path):
    first = create_session_directory(tmp_path)
    second = create_session_directory(tmp_path)
    assert first.parent == tmp_path
    assert second.parent == tmp_path
    assert first != second
    assert first.name.startswith("edge-")


def test_latest_frame_buffer_replaces_stale_frames_without_queueing():
    frames = LatestFrameBuffer()
    assert frames.put("first") is False
    assert frames.put("newest") is True
    assert frames.take() == "newest"
    assert frames.take() is None
    frames.put("discard")
    frames.clear()
    assert frames.take() is None


def test_rtp_metrics_classifies_wrap_gap_duplicate_and_access_units():
    metrics = RtpMetrics(expected_payload_type=96)

    def packet(sequence, marker=False, payload=b"abc"):
        return bytes([0x80, 0xE0 if marker else 0x60]) + sequence.to_bytes(2, "big") + (123).to_bytes(4, "big") + (456).to_bytes(4, "big") + payload

    assert metrics.observe(packet(65535)) is not None
    assert metrics.observe(packet(0, marker=True)) is not None
    assert metrics.observe(packet(2, marker=True)) is not None
    assert metrics.observe(packet(2, marker=True)) is not None
    assert metrics.observe(b"bad") is None

    snapshot = metrics.snapshot()
    assert snapshot["packets_received"] == 4
    assert snapshot["bytes_received"] == 60
    assert snapshot["sequence_gaps"] == 1
    assert snapshot["duplicates"] == 1
    assert snapshot["access_units_observed"] == 2
    assert snapshot["access_unit_bytes_total"] == 45
    assert snapshot["packets_rejected"] == 1


def test_rtp_bitrate_uses_the_bounded_recent_measurement_window():
    metrics = RtpMetrics(expected_payload_type=96)

    def packet(sequence, payload):
        return bytes([0x80, 0xE0]) + sequence.to_bytes(2, "big") + (123).to_bytes(4, "big") + (456).to_bytes(4, "big") + payload

    # 121 access units force the oldest byte count out of the 120-AU window.
    for sequence in range(121):
        metrics.observe(packet(sequence, b"x" * (1000 if sequence == 0 else 1)), receive_mono_ns=sequence * 1_000_000_000)

    snapshot = metrics.snapshot()
    assert snapshot["estimated_fps"] == pytest.approx(1.0)
    # The metric accounts for the complete RTP datagram: 12-byte header + 1 byte payload.
    assert snapshot["estimated_bitrate_bps"] == pytest.approx(120 * 13 * 8 / 119)


def test_raw_rtp_capture_uses_length_prefixed_records_and_is_opt_in(tmp_path):
    disabled = RawRtpCapture(None)
    assert disabled.enabled is False
    assert disabled.write(b"ignored") is False

    path = tmp_path / "primary.rtpbin"
    capture = RawRtpCapture(path)
    assert capture.enabled is True
    assert capture.write(b"one") is True
    assert capture.write(b"two-two") is True
    capture.close()

    payload = path.read_bytes()
    assert payload[:8] == b"DJIRTP01"
    first_size = struct.unpack(">I", payload[8:12])[0]
    assert payload[12:12 + first_size] == b"one"
    second_at = 12 + first_size
    second_size = struct.unpack(">I", payload[second_at:second_at + 4])[0]
    assert payload[second_at + 4:second_at + 4 + second_size] == b"two-two"


def test_navigation_prefers_active_rtk_but_keeps_aircraft_relative_altitude():
    snapshot = {
        "session": "session-a",
        "flight": {
            "android_mono_ns": 100,
            "edge_receive_mono_ns": 150,
            "mapped_edge_mono_ns": 140,
            "data": {"fields": {"aircraft.latitude_deg": {"value": 38.0}, "aircraft.longitude_deg": {"value": -9.0}, "aircraft.altitude_m": {"value": 12.5}, "heading_deg": {"value": 42.0}, "velocity.north_m_s": {"value": 1.0}, "velocity.east_m_s": {"value": 2.0}, "velocity.down_m_s": {"value": 3.0}, "gps.signal_level": {"value": 5}}},
        },
        "rtk": {
            "android_mono_ns": 110,
            "edge_receive_mono_ns": 160,
            "mapped_edge_mono_ns": 155,
            "data": {"fields": {"fusion.latitude_deg": {"value": 38.1}, "fusion.longitude_deg": {"value": -9.1}, "is_being_used": {"value": True}}},
        },
        "gimbal": {
            "android_mono_ns": 115,
            "data": {"fields": {"attitude.pitch_deg": {"value": -42.5}}},
        },
    }
    navigation = build_navigation(snapshot)
    assert navigation["position_source"] == "rtk"
    assert navigation["latitude_deg"] == 38.1
    assert navigation["altitude_m"] == 12.5
    assert navigation["transport_age_s"] == pytest.approx(5e-9)
    assert navigation["gimbal_pitch_valid"] is True
    assert navigation["gimbal_pitch_deg"] == -42.5
    assert navigation["gimbal_android_mono_ns"] == 115


def test_navigation_falls_back_to_gps_and_rejects_missing_position():
    gps_snapshot = {"session": "session-b", "rtk": None, "flight": {"android_mono_ns": 10, "edge_receive_mono_ns": 20, "mapped_edge_mono_ns": None, "data": {"fields": {"aircraft.latitude_deg": {"value": 37.0}, "aircraft.longitude_deg": {"value": -8.0}}}}}
    navigation = build_navigation(gps_snapshot)
    assert navigation["position_source"] == "gps_fallback"
    assert navigation["rtk_valid"] is False
    assert navigation["gimbal_pitch_valid"] is False
    assert navigation["transport_age_s"] is None
    assert build_navigation({"flight": None, "rtk": None}) is None


def test_dashboard_serves_state_health_and_clean_exit_callback():
    exits = []
    dashboard = DashboardServer("127.0.0.1", 0, lambda: {"status": "ok", "video": []}, lambda: exits.append(True))
    dashboard.start()
    try:
        base = f"http://127.0.0.1:{dashboard.port}"
        assert json.loads(urlopen(f"{base}/v1/state", timeout=2).read()) == {"status": "ok", "video": []}
        assert json.loads(urlopen(f"{base}/health", timeout=2).read())["status"] == "ok"
        page = urlopen(f"{base}/", timeout=2).read().decode()
        assert "DJI Transport Edge" in page
        assert "Configuration and raw diagnostics" in page
        request = Request(f"{base}/v1/exit", method="POST")
        assert json.loads(urlopen(request, timeout=2).read()) == {"stopping": True}
        assert exits == [True]
    finally:
        dashboard.close()
