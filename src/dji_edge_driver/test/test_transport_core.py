import json
from pathlib import Path
import socket
import struct
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from dji_edge_transport_core.clock import ClockMapper
from dji_edge_transport_core.clock_client import ClockPinger
from dji_edge_transport_core.dashboard import DashboardServer, start_optional_dashboard
from dji_edge_transport_core.evidence import EvidenceWriter, create_session_directory
from dji_edge_transport_core import local_config
from dji_edge_transport_core.local_config import atomic_write, resolve_ipv4_udp_target, save_requested_config, validate
from dji_edge_transport_core import mapper_config
from dji_edge_transport_core.mapper_config import atomic_write as atomic_write_mapper_config
from dji_edge_transport_core.mapper_config import load as load_mapper_config
from dji_edge_transport_core.mapper_config import save_requested_config as save_mapper_config
from dji_edge_transport_core.mapper_config import validate as validate_mapper_config
from dji_edge_transport_core.mapper_status import MapperStatusCache
from dji_edge_transport_core.navigation import build_navigation
from dji_edge_transport_core.protocol import ProtocolError, decode_json_packet, parse_rtp_packet, video_au_identity
from dji_edge_transport_core.rtp import RawRtpCapture, RtpMetrics
from dji_edge_transport_core.state import IngressMetrics, LatestState, SequenceTracker
from dji_edge_transport_core.synchronization import TemporalCorrelation
from dji_edge_transport_core.video import LatestFrameBuffer, RtpPtsBinding


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


def test_clock_pinger_receives_android_reply_on_its_ephemeral_source_socket():
    """Android replies to the source endpoint of a clock_ping, not UDP 5502."""
    responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responder.bind(("127.0.0.1", 0))
    received = {}

    def reply_once():
        request, remote = responder.recvfrom(1201)
        ping = json.loads(request)
        received["remote"] = remote
        responder.sendto(json.dumps({
            "v": 1, "type": "clock_pong", "session": ping["session"],
            "stream": ping["stream"], "seq": ping["seq"],
            "t0_edge_send_mono_ns": ping["t0_edge_send_mono_ns"],
            "t1_android_rx_mono_ns": 100, "t2_android_tx_mono_ns": 101,
        }, separators=(",", ":")).encode(), remote)

    thread = threading.Thread(target=reply_once)
    thread.start()
    responder_port = responder.getsockname()[1]
    try:
        pinger = ClockPinger(timeout_s=1.0)
        try:
            exchange = pinger.exchange_once(
                ("127.0.0.1", responder_port), "edge-clock", 7,
            )
        finally:
            pinger.close()
    finally:
        thread.join(timeout=2)
        responder.close()

    response = json.loads(exchange.response_data)
    assert received["remote"][1] != responder_port
    assert exchange.remote[0] == "127.0.0.1"
    assert response["type"] == "clock_pong"
    assert response["seq"] == 7
    assert exchange.t3_edge_receive_mono_ns > exchange.t0_edge_send_mono_ns
    assert response["t0_edge_send_mono_ns"] == exchange.t0_edge_send_mono_ns


def test_clock_pinger_reuses_its_source_socket_and_ignores_a_late_pong():
    """A delayed Android pong must not poison the next clock exchange."""
    responder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responder.bind(("127.0.0.1", 0))
    remotes = []

    def pong(ping):
        return json.dumps({
            "v": 1, "type": "clock_pong", "session": ping["session"],
            "stream": ping["stream"], "seq": ping["seq"],
            "t0_edge_send_mono_ns": ping["t0_edge_send_mono_ns"],
            "t1_android_rx_mono_ns": 100, "t2_android_tx_mono_ns": 101,
        }, separators=(",", ":")).encode()

    def reply_late_then_current():
        first_raw, first_remote = responder.recvfrom(1201)
        remotes.append(first_remote)
        second_raw, second_remote = responder.recvfrom(1201)
        remotes.append(second_remote)
        responder.sendto(pong(json.loads(first_raw)), second_remote)
        responder.sendto(pong(json.loads(second_raw)), second_remote)

    thread = threading.Thread(target=reply_late_then_current, daemon=True)
    thread.start()
    responder_port = responder.getsockname()[1]
    try:
        pinger = ClockPinger(timeout_s=0.05)
        try:
            with pytest.raises(socket.timeout):
                pinger.exchange_once(("127.0.0.1", responder_port), "edge-clock", 1)
            exchange = pinger.exchange_once(
                ("127.0.0.1", responder_port), "edge-clock", 2,
                lambda candidate: json.loads(candidate.response_data)["seq"] == 2,
            )
        finally:
            pinger.close()
    finally:
        thread.join(timeout=2)
        responder.close()

    assert remotes[0] == remotes[1]
    assert json.loads(exchange.response_data)["seq"] == 2


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


def test_rtp_pts_binding_fails_closed_for_missing_or_ambiguous_pipeline_time():
    binding = RtpPtsBinding(max_entries=2)
    assert binding.observe(101, 7, 11) is True
    assert binding.resolve(101) == (7, 11)
    assert binding.resolve(999) is None
    assert binding.observe(102, 7, 12) is True
    assert binding.observe(102, 7, 13) is False
    assert binding.resolve(102) is None
    assert binding.snapshot()["ambiguous"] == 1


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


def test_rtp_metrics_exposes_observed_datagram_age_even_when_packet_is_rejected():
    metrics = RtpMetrics(expected_payload_type=96)
    assert metrics.observe(b"invalid", receive_mono_ns=100) is None

    snapshot = metrics.snapshot(now_mono_ns=2_100_000_100)
    assert snapshot["datagrams_observed"] == 1
    assert snapshot["packets_received"] == 0
    assert snapshot["packets_rejected"] == 1
    assert snapshot["last_datagram_age_s"] == pytest.approx(2.1)


def test_ingress_metrics_keeps_post_network_received_valid_and_rejected_facts_separate():
    metrics = IngressMetrics(("telemetry", "frame_metadata"))
    metrics.observe("telemetry", 100)
    metrics.accept("telemetry")
    metrics.observe("frame_metadata", 200)
    metrics.reject("frame_metadata")

    snapshot = metrics.snapshot(now_mono_ns=1_000_000_200)
    assert snapshot["telemetry"] == {"datagrams_received": 1, "packets_valid": 1, "packets_rejected": 0, "last_datagram_age_s": pytest.approx(1.0)}
    assert snapshot["frame_metadata"] == {"datagrams_received": 1, "packets_valid": 0, "packets_rejected": 1, "last_datagram_age_s": pytest.approx(1.0)}


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


def test_temporal_correlation_interpolates_frame_navigation_and_heading_wrap():
    correlation = TemporalCorrelation(max_samples=8, max_gap_ns=1_000)
    correlation.start_session("s")
    correlation.add_telemetry("flight", {"android_mono_ns": 100, "data": {"fields": {
        "aircraft.latitude_deg": {"value": 38.0}, "aircraft.longitude_deg": {"value": -9.0},
        "aircraft.altitude_m": {"value": 10.0}, "heading_deg": {"value": 350.0},
    }}})
    correlation.add_telemetry("flight", {"android_mono_ns": 300, "data": {"fields": {
        "aircraft.latitude_deg": {"value": 38.2}, "aircraft.longitude_deg": {"value": -8.8},
        "aircraft.altitude_m": {"value": 14.0}, "heading_deg": {"value": 10.0},
    }}})
    correlation.add_telemetry("gimbal", {"android_mono_ns": 100, "data": {"fields": {"attitude.pitch_deg": {"value": -40.0}}}})
    correlation.add_telemetry("gimbal", {"android_mono_ns": 300, "data": {"fields": {"attitude.pitch_deg": {"value": -20.0}}}})
    context = correlation.associate_android_time(200)
    assert context["association_quality"] == "interpolated"
    assert context["position_source"] == "gps_fallback"
    assert context["latitude_deg"] == pytest.approx(38.1)
    assert context["altitude_m"] == pytest.approx(12.0)
    assert context["heading_deg"] == pytest.approx(0.0)
    assert context["gimbal_pitch_deg"] == pytest.approx(-30.0)


def test_temporal_correlation_ignores_edge_delivery_observations():
    correlation = TemporalCorrelation(max_samples=4, max_gap_ns=1_000)
    correlation.start_session("s")
    correlation.add_telemetry("flight", {"android_mono_ns": 100, "edge_receive_mono_ns": 9_000_000, "data": {"fields": {
        "aircraft.latitude_deg": {"value": 38.0}, "aircraft.longitude_deg": {"value": -9.0},
        "aircraft.altitude_m": {"value": 10.0}, "heading_deg": {"value": 350.0},
    }}})
    correlation.add_telemetry("flight", {"android_mono_ns": 300, "edge_receive_mono_ns": 1, "data": {"fields": {
        "aircraft.latitude_deg": {"value": 38.2}, "aircraft.longitude_deg": {"value": -8.8},
        "aircraft.altitude_m": {"value": 14.0}, "heading_deg": {"value": 10.0},
    }}})
    context = correlation.associate_android_time(200)
    assert context["latitude_deg"] == pytest.approx(38.1)
    assert context["heading_deg"] == pytest.approx(0.0)


def test_temporal_correlation_prefers_relevant_rtk_and_fails_closed_when_stale():
    correlation = TemporalCorrelation(max_samples=4, max_gap_ns=100)
    correlation.start_session("s")
    correlation.add_telemetry("flight", {"android_mono_ns": 100, "data": {"fields": {
        "aircraft.latitude_deg": {"value": 38.0}, "aircraft.longitude_deg": {"value": -9.0},
        "aircraft.altitude_m": {"value": 10.0}, "heading_deg": {"value": 10.0},
    }}})
    correlation.add_telemetry("rtk", {"android_mono_ns": 100, "data": {"fields": {
        "fusion.latitude_deg": {"value": 38.01}, "fusion.longitude_deg": {"value": -9.01}, "is_being_used": {"value": True},
    }}})
    selected = correlation.associate_android_time(110)
    assert selected["association_quality"] == "nearest"
    assert selected["position_source"] == "rtk"
    assert selected["latitude_deg"] == pytest.approx(38.01)
    stale = correlation.associate_android_time(1_000)
    assert stale["association_quality"] == "unavailable"
    assert stale["position_valid"] is False


def test_temporal_correlation_is_bounded_and_clears_on_session_change():
    correlation = TemporalCorrelation(max_samples=2, max_gap_ns=100)
    correlation.start_session("first")
    for timestamp in (1, 2, 3):
        correlation.add_telemetry("flight", {"android_mono_ns": timestamp, "data": {"fields": {}}})
    assert correlation.snapshot()["history_sizes"]["flight"] == 2
    correlation.start_session("second")
    assert correlation.snapshot()["history_sizes"]["flight"] == 0


def test_latest_state_associates_rtp_au_to_frame_time_not_latest_state():
    state, sequences = LatestState(ClockMapper()), SequenceTracker()

    def ingest(raw, received):
        packet = decode_json_packet(json.dumps(raw).encode(), 1)
        sequence = sequences.observe((packet.session, packet.packet_type, packet.stream), packet.sequence)
        assert state.update_packet(packet, received, ("127.0.0.1", 5500), sequence)

    common = {"v": 1, "session": "s", "stream": "flight:0"}
    ingest({**common, "type": "flight", "seq": 1, "rx_mono_ns": 100, "data": {"fields": {
        "aircraft.latitude_deg": {"value": 38.0}, "aircraft.longitude_deg": {"value": -9.0}, "aircraft.altitude_m": {"value": 10.0}, "heading_deg": {"value": 5.0},
    }}}, 200)
    ingest({**common, "type": "flight", "seq": 2, "rx_mono_ns": 300, "data": {"fields": {
        "aircraft.latitude_deg": {"value": 38.2}, "aircraft.longitude_deg": {"value": -8.8}, "aircraft.altitude_m": {"value": 14.0}, "heading_deg": {"value": 15.0},
    }}}, 400)
    ingest({"v": 1, "type": "video_au", "session": "s", "feed": "primary", "frame_seq": 7, "rtp_ssrc": 88, "rtp_ts": 99, "au_first_byte_rx_mono_ns": 190, "au_complete_rx_mono_ns": 200}, 500)
    identity, navigation = state.associate_frame("primary", 88, 99)
    assert identity["frame_seq"] == 7
    assert navigation["latitude_deg"] == pytest.approx(38.1)
    assert navigation["heading_deg"] == pytest.approx(10.0)
    assert state.associate_frame("primary", 88, 100) is None


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
        assert "Android ingress" in page
        assert "waiting for Android" in page
        request = Request(f"{base}/v1/exit", method="POST")
        assert json.loads(urlopen(request, timeout=2).read()) == {"stopping": True}
        assert exits == [True]
    finally:
        dashboard.close()


def test_dashboard_configuration_api_calls_only_validated_saver():
    saved = []
    dashboard = DashboardServer(
        "127.0.0.1", 0, lambda: {"status": "ok", "video": []}, lambda: None,
        lambda: {"values": {"android_clock_host": "", "capture_rtp": False, "preview_windows": True}},
        lambda values, restart: saved.append((values, restart)) or {"status": "saved", "restarting": restart},
    )
    dashboard.start()
    try:
        base = f"http://127.0.0.1:{dashboard.port}"
        assert json.loads(urlopen(f"{base}/v1/config", timeout=2).read())["values"]["capture_rtp"] is False
        request = Request(f"{base}/v1/config", data=json.dumps({"values": {"android_clock_host": "192.168.1.2", "capture_rtp": True, "preview_windows": False}, "restart": True}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        assert json.loads(urlopen(request, timeout=2).read())["restarting"] is True
        assert saved == [({"android_clock_host": "192.168.1.2", "capture_rtp": True, "preview_windows": False}, True)]
    finally:
        dashboard.close()


def test_dashboard_rejects_non_boolean_restart_flag():
    saved = []
    dashboard = DashboardServer(
        "127.0.0.1", 0, lambda: {"status": "ok", "video": []}, lambda: None,
        lambda: {"values": {"android_clock_host": "", "capture_rtp": False, "preview_windows": True}},
        lambda values, restart: saved.append((values, restart)) or {"status": "saved", "restarting": restart},
    )
    dashboard.start()
    try:
        request = Request(
            f"http://127.0.0.1:{dashboard.port}/v1/config",
            data=json.dumps({"values": {"android_clock_host": "192.168.1.151", "capture_rtp": True, "preview_windows": False}, "restart": "false"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with pytest.raises(HTTPError) as raised:
            urlopen(request, timeout=2)
        assert raised.value.code == 400
        assert saved == []
    finally:
        dashboard.close()


def test_dashboard_rejects_a_non_object_configuration_request():
    dashboard = DashboardServer(
        "127.0.0.1", 0, lambda: {"status": "ok", "video": []}, lambda: None,
        lambda: {"values": {"android_clock_host": "", "capture_rtp": False, "preview_windows": True}},
        lambda values, restart: {"status": "saved", "restarting": restart},
    )
    dashboard.start()
    try:
        request = Request(
            f"http://127.0.0.1:{dashboard.port}/v1/config",
            data=b"[]", headers={"Content-Type": "application/json"}, method="POST",
        )
        with pytest.raises(HTTPError) as raised:
            urlopen(request, timeout=2)
        assert raised.value.code == 400
        assert "object" in json.loads(raised.value.read())["error"]
    finally:
        dashboard.close()


def test_local_dashboard_config_validates_and_writes_atomically(tmp_path):
    target = tmp_path / "bridge.local.yaml"
    values = {"android_clock_host": "192.168.1.151", "capture_rtp": True, "preview_windows": False}
    atomic_write(target, values)
    assert 'android_clock_host: "192.168.1.151"' in target.read_text()
    before = target.read_text()
    with pytest.raises(ValueError):
        atomic_write(target, {"android_clock_host": "bad host!", "capture_rtp": True, "preview_windows": False})
    assert target.read_text() == before


def test_local_dashboard_config_rejects_ipv6_but_accepts_ipv4_and_hostname():
    base = {"capture_rtp": False, "preview_windows": True}
    assert validate({**base, "android_clock_host": "192.168.1.151"})["android_clock_host"] == "192.168.1.151"
    assert validate({**base, "android_clock_host": "tablet.local"})["android_clock_host"] == "tablet.local"
    with pytest.raises(ValueError, match="IPv4"):
        validate({**base, "android_clock_host": "2001:db8::1"})


def test_clock_target_resolution_is_explicitly_ipv4(monkeypatch):
    calls = []

    def resolve(host, port, *, family, type):
        calls.append((host, port, family, type))
        return [(family, type, 17, "", ("192.168.1.151", port))]

    monkeypatch.setattr(local_config.socket, "getaddrinfo", resolve)
    assert resolve_ipv4_udp_target("tablet.local", 5502) == ("192.168.1.151", 5502)
    assert calls == [("tablet.local", 5502, local_config.socket.AF_INET, local_config.socket.SOCK_DGRAM)]


def test_atomic_config_write_failure_keeps_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "bridge.local.yaml"
    target.write_text("known-good\n", encoding="utf-8")
    monkeypatch.setattr(local_config.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        atomic_write(target, {"android_clock_host": "192.168.1.151", "capture_rtp": True, "preview_windows": False})
    assert target.read_text(encoding="utf-8") == "known-good\n"


def test_restart_marker_failure_does_not_request_driver_stop(tmp_path, monkeypatch):
    target = tmp_path / "bridge.local.yaml"
    marker = tmp_path / "restart.request"
    stopped = []
    original_write_text = Path.write_text

    def fail_marker(path, *args, **kwargs):
        if path == marker:
            raise OSError("marker volume is read-only")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_marker)
    with pytest.raises(OSError, match="restart was not requested"):
        save_requested_config(
            {"android_clock_host": "192.168.1.151", "capture_rtp": True, "preview_windows": False},
            target,
            marker,
            restart=True,
            request_stop=lambda: stopped.append(True),
        )
    assert stopped == []
    assert 'android_clock_host: "192.168.1.151"' in target.read_text(encoding="utf-8")


def test_dashboard_returns_http_error_when_config_saver_has_filesystem_failure():
    def fail_save(_values, _restart):
        raise OSError("disk full")

    dashboard = DashboardServer(
        "127.0.0.1", 0, lambda: {"status": "ok", "video": []}, lambda: None,
        lambda: {"values": {"android_clock_host": "", "capture_rtp": False, "preview_windows": True}},
        fail_save,
    )
    dashboard.start()
    try:
        request = Request(
            f"http://127.0.0.1:{dashboard.port}/v1/config",
            data=json.dumps({"values": {"android_clock_host": "192.168.1.151", "capture_rtp": True, "preview_windows": False}, "restart": False}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with pytest.raises(HTTPError) as raised:
            urlopen(request, timeout=2)
        assert raised.value.code == 500
        assert "disk full" in json.loads(raised.value.read())["error"]
    finally:
        dashboard.close()


def test_busy_dashboard_port_is_reported_without_raising():
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reservation.bind(("127.0.0.1", 0))
    errors = []
    try:
        dashboard = start_optional_dashboard(
            "127.0.0.1", reservation.getsockname()[1], lambda: {"status": "ok"}, lambda: None,
            on_error=lambda error: errors.append(str(error)),
        )
        assert dashboard is None
        assert errors
    finally:
        reservation.close()


@pytest.mark.parametrize("values", [
    {"android_clock_host": "", "capture_rtp": "true", "preview_windows": False},
    {"android_clock_host": "127.0.0.1", "capture_rtp": False, "preview_windows": True, "port": 5600},
])
def test_local_dashboard_config_rejects_outside_allowlist(values):
    with pytest.raises(ValueError):
        validate(values)


def test_mapper_dashboard_config_is_narrow_and_preserves_unlimited_history(tmp_path):
    target = tmp_path / "mapper.runtime.local.yaml"
    values = {"min_path_spacing_m": 0.15, "max_history_points": 0}
    atomic_write_mapper_config(target, values)
    rendered = target.read_text(encoding="utf-8")
    assert "min_path_spacing_m: 0.15" in rendered
    assert "max_history_points: 0" in rendered
    assert "map_metadata_path" not in rendered
    assert load_mapper_config(target) == values


@pytest.mark.parametrize("values", [
    {"min_path_spacing_m": -0.1, "max_history_points": 5},
    {"min_path_spacing_m": float("inf"), "max_history_points": 5},
    {"min_path_spacing_m": 0.1, "max_history_points": -1},
    {"min_path_spacing_m": 0.1, "max_history_points": 2.0},
    {"min_path_spacing_m": 0.1, "max_history_points": 5, "utm_zone": 29},
])
def test_mapper_dashboard_config_rejects_unsafe_or_invalid_values(values):
    with pytest.raises(ValueError):
        validate_mapper_config(values)


def test_mapper_dashboard_config_atomic_failure_keeps_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "mapper.runtime.local.yaml"
    target.write_text("known-good\n", encoding="utf-8")
    monkeypatch.setattr(mapper_config.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        atomic_write_mapper_config(target, {"min_path_spacing_m": 0.1, "max_history_points": 20})
    assert target.read_text(encoding="utf-8") == "known-good\n"


def test_mapper_restart_marker_failure_keeps_stack_running(tmp_path, monkeypatch):
    target = tmp_path / "mapper.runtime.local.yaml"
    marker = tmp_path / "restart.request"
    stopped = []
    original_write_text = Path.write_text

    def fail_marker(path, *args, **kwargs):
        if path == marker:
            raise OSError("marker volume is read-only")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_marker)
    with pytest.raises(OSError, match="restart was not requested"):
        save_mapper_config(
            {"min_path_spacing_m": 0.1, "max_history_points": 20}, target, marker,
            restart=True, request_stop=lambda: stopped.append(True),
        )
    assert stopped == []
    assert load_mapper_config(target) == {"min_path_spacing_m": 0.1, "max_history_points": 20}


def test_mapper_status_cache_is_bounded_and_keeps_last_valid_snapshot():
    cache = MapperStatusCache()
    valid = {
        "accepted": 3,
        "accepted_rtk": 2,
        "received": 3,
        "path_poses": 4,
        "active_min_path_spacing_m": 0.15,
        "active_max_history_points": 5000,
    }
    cache.observe(json.dumps(valid), 1_000_000_000)
    snapshot = cache.snapshot(3.0, 2_000_000_000)
    assert snapshot["state"] == "ok"
    assert snapshot["active_values"] == {"min_path_spacing_m": 0.15, "max_history_points": 5000}
    cache.observe('{"accepted": -1}', 2_000_000_000)
    invalid = cache.snapshot(3.0, 2_000_000_000)
    assert invalid["state"] == "invalid"
    assert invalid["values"] == valid
    cache.observe(json.dumps({"accepted": 5, "received": 5, "path_poses": 5}), 2_000_000_000)
    assert cache.snapshot(3.0, 6_000_000_001)["state"] == "stale"


@pytest.mark.parametrize("invalid_active_values", [
    {"active_min_path_spacing_m": 0.1},
    {"active_max_history_points": 5},
    {"active_min_path_spacing_m": -0.1, "active_max_history_points": 5},
    {"active_min_path_spacing_m": float("nan"), "active_max_history_points": 5},
    {"active_min_path_spacing_m": 0.1, "active_max_history_points": -1},
    {"active_min_path_spacing_m": 0.1, "active_max_history_points": 5.0},
])
def test_mapper_status_cache_rejects_invalid_or_partial_active_values(invalid_active_values):
    cache = MapperStatusCache()
    baseline = {"accepted": 1, "received": 1, "path_poses": 1}
    cache.observe(json.dumps(baseline), 1_000_000_000)

    cache.observe(json.dumps({**baseline, **invalid_active_values}), 2_000_000_000)

    snapshot = cache.snapshot(3.0, 2_000_000_000)
    assert snapshot["state"] == "invalid"
    assert snapshot["values"] == baseline
    assert snapshot["active_values"] is None


def test_dashboard_mapper_configuration_api_is_separate_and_safe():
    saved = []
    dashboard = DashboardServer(
        "127.0.0.1", 0, lambda: {"status": "ok", "video": []}, lambda: None,
        mapper_config_provider=lambda: {
            "values": {"min_path_spacing_m": 0.15, "max_history_points": 5000},
            "requested_values": {"min_path_spacing_m": 0.15, "max_history_points": 5000},
            "active_values": {"min_path_spacing_m": 0.15, "max_history_points": 5000},
            "pending_restart": False,
            "status": {"state": "ok", "values": {"frame_context_received": 4, "frame_context_published": 3, "frame_context_rejected": 1, "frame_context_unavailable": 0}},
        },
        mapper_config_saver=lambda values, restart: saved.append((values, restart)) or {"status": "saved", "restarting": restart},
    )
    dashboard.start()
    try:
        base = f"http://127.0.0.1:{dashboard.port}"
        payload = json.loads(urlopen(f"{base}/v1/mapper-config", timeout=2).read())
        assert payload["values"]["max_history_points"] == 5000
        request = Request(
            f"{base}/v1/mapper-config",
            data=json.dumps({"values": {"min_path_spacing_m": 0.2, "max_history_points": 0}, "restart": True}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        assert json.loads(urlopen(request, timeout=2).read())["restarting"] is True
        assert saved == [({"min_path_spacing_m": 0.2, "max_history_points": 0}, True)]
        page = urlopen(f"{base}/", timeout=2).read().decode()
        assert "Map &amp; Path" in page
        assert "map_metadata_path" not in page
        assert "mapperDraftDirty" in page
        assert "Active path" in page
        assert "Pending override" in page
        assert "frame_context_received" in page
    finally:
        dashboard.close()


def test_dashboard_mapper_configuration_rejects_invalid_requests():
    dashboard = DashboardServer(
        "127.0.0.1", 0, lambda: {"status": "ok", "video": []}, lambda: None,
        mapper_config_provider=lambda: {"values": {"min_path_spacing_m": 0.15, "max_history_points": 5000}},
        mapper_config_saver=lambda values, restart: validate_mapper_config(values) or {"restarting": restart},
    )
    dashboard.start()
    try:
        request = Request(
            f"http://127.0.0.1:{dashboard.port}/v1/mapper-config",
            data=json.dumps({"values": {"min_path_spacing_m": -1, "max_history_points": 5}, "restart": False}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with pytest.raises(HTTPError) as raised:
            urlopen(request, timeout=2)
        assert raised.value.code == 400
    finally:
        dashboard.close()
