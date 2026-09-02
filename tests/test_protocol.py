import json
import unittest

from dji_edge_receiver.protocol import ProtocolError, decode_json_packet, parse_rtp_packet


def rtp_packet(sequence=7, timestamp=9000, ssrc=0x12345678, payload_type=96, marker=True):
    return bytes(
        [0x80, (0x80 if marker else 0) | payload_type]
    ) + sequence.to_bytes(2, "big") + timestamp.to_bytes(4, "big") + ssrc.to_bytes(4, "big") + b"\x65payload"


class ProtocolTest(unittest.TestCase):
    def test_compact_telemetry(self):
        raw = json.dumps(
            {
                "v": 1,
                "type": "flight",
                "session": "s1",
                "stream": "aircraft",
                "seq": 2,
                "rx_mono_ns": 100,
                "valid": True,
                "lat": 38.0,
            }
        ).encode()
        packet = decode_json_packet(raw, 1)
        self.assertEqual(packet.sequence, 2)
        self.assertEqual(packet.body["lat"], 38.0)

    def test_frame_sidecar_aliases(self):
        raw = json.dumps(
            {
                "v": 1,
                "type": "video_au",
                "session": "s1",
                "feed": "primary",
                "frame_seq": 3,
                "rtp_ssrc": 10,
                "rtp_ts": 90,
                "au_first_byte_rx_mono_ns": 100,
                "au_complete_rx_mono_ns": 110,
            }
        ).encode()
        packet = decode_json_packet(raw, 1)
        self.assertEqual(packet.stream, "primary")
        self.assertEqual(packet.android_mono_ns, 100)

    def test_rejects_bad_version_and_timestamp(self):
        base = {"v": 2, "type": "flight", "session": "s", "seq": 0, "rx_mono_ns": 1}
        with self.assertRaises(ProtocolError):
            decode_json_packet(json.dumps(base).encode(), 1)
        base.update(v=1, rx_mono_ns=0)
        with self.assertRaises(ProtocolError):
            decode_json_packet(json.dumps(base).encode(), 1)

    def test_clock_packets_require_complete_ordered_timestamps(self):
        ping = {"v": 1, "type": "clock_ping", "session": "s", "stream": "clock", "seq": 1}
        with self.assertRaises(ProtocolError):
            decode_json_packet(json.dumps(ping).encode(), 1)
        pong = {
            **ping, "type": "clock_pong", "t0_edge_send_mono_ns": 10,
            "t1_android_rx_mono_ns": 30, "t2_android_tx_mono_ns": 20,
        }
        with self.assertRaises(ProtocolError):
            decode_json_packet(json.dumps(pong).encode(), 1)

    def test_rtp_header(self):
        packet = parse_rtp_packet(rtp_packet(), 96)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.sequence, 7)
        self.assertEqual(packet.ssrc, 0x12345678)
        self.assertTrue(packet.marker)

    def test_rtp_rejects_wrong_version_and_payload_type(self):
        bad = bytearray(rtp_packet())
        bad[0] = 0x40
        with self.assertRaises(ProtocolError):
            parse_rtp_packet(bytes(bad), 96)
        with self.assertRaises(ProtocolError):
            parse_rtp_packet(rtp_packet(payload_type=97), 96)


if __name__ == "__main__":
    unittest.main()
