import json

import pytest

from dji_edge_transport_core.clock import ClockMapper
from dji_edge_transport_core.protocol import ProtocolError, decode_json_packet, parse_rtp_packet
from dji_edge_transport_core.state import LatestState, SequenceTracker


def test_golden_control_packets_decode_with_stable_envelopes():
    # Contract values mirrored from deploy/protocol/v1/golden_packets.json so
    # this ROS package stays testable when ros2_ws alone is mounted in Docker.
    packets = [
        {"v": 1, "type": "flight", "session": "fixture-session-0001", "seq": 42, "rx_mono_ns": 1234567890123, "lat": 38.7223},
        {"v": 1, "type": "rtk", "session": "fixture-session-0001", "seq": 11, "rx_mono_ns": 1234567890220, "lat": 38.7223001},
        {"v": 1, "type": "gimbal", "session": "fixture-session-0001", "seq": 77, "rx_mono_ns": 1234567890330, "pitch_deg": -45.25},
        {"v": 1, "type": "health", "session": "fixture-session-0001", "seq": 3, "rx_mono_ns": 1234567890440, "sender_drops": 0},
        {"v": 1, "type": "video_au", "session": "fixture-session-0001", "feed": "primary", "source": "FPV_CAM", "frame_seq": 9321, "rtp_ssrc": 305419896, "rtp_ts": 987654321, "au_first_byte_rx_mono_ns": 1234567890500, "au_complete_rx_mono_ns": 1234567891987},
    ]
    decoded = [decode_json_packet(json.dumps(packet).encode(), 1) for packet in packets]
    assert [packet.packet_type for packet in decoded] == ["flight", "rtk", "gimbal", "health", "video_au"]
    assert decoded[-1].stream == "primary"
    assert decoded[-1].body["rtp_ssrc"] == 305419896


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
