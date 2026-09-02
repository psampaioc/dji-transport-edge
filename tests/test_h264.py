import unittest

from dji_edge_receiver.h264 import H264RtpAnalyzer, parse_sps_dimensions


DJI_PRIMARY_SPS = bytes.fromhex("274d60298d6805005ba6a0202028000003000800000301e078a115")


class H264AnalyzerTest(unittest.TestCase):
    def test_real_dji_sps_dimensions(self):
        info = parse_sps_dimensions(DJI_PRIMARY_SPS)
        self.assertEqual(info["width"], 1280)
        self.assertEqual(info["height"], 720)
        self.assertEqual(info["profile_idc"], 77)
        self.assertEqual(info["level_idc"], 41)

    def test_single_nals_and_idr_age(self):
        analyzer = H264RtpAnalyzer()
        analyzer.observe(DJI_PRIMARY_SPS, now_ns=1_000_000)
        analyzer.observe(b"\x68pps", now_ns=2_000_000)
        analyzer.observe(b"\x65idr", now_ns=3_000_000)
        state = analyzer.snapshot(now_ns=5_000_000)
        self.assertEqual(state["stream_info"]["width"], 1280)
        self.assertEqual(state["sps_count"], 1)
        self.assertEqual(state["pps_count"], 1)
        self.assertEqual(state["idr_count"], 1)
        self.assertEqual(state["last_idr_age_ms"], 2.0)

    def test_stap_a_and_fragmented_sps(self):
        analyzer = H264RtpAnalyzer()
        pps = b"\x68pps"
        stap = b"\x78" + len(pps).to_bytes(2, "big") + pps
        analyzer.observe(stap)

        indicator = bytes([(DJI_PRIMARY_SPS[0] & 0xE0) | 28])
        nal_type = DJI_PRIMARY_SPS[0] & 0x1F
        body = DJI_PRIMARY_SPS[1:]
        split = len(body) // 2
        analyzer.observe(indicator + bytes([0x80 | nal_type]) + body[:split])
        analyzer.observe(indicator + bytes([0x40 | nal_type]) + body[split:])
        state = analyzer.snapshot()
        self.assertEqual(state["pps_count"], 1)
        self.assertEqual(state["sps_count"], 1)
        self.assertEqual(state["stream_info"]["height"], 720)

    def test_discontinuity_discards_partial_fragment(self):
        analyzer = H264RtpAnalyzer()
        analyzer.observe(b"\x7c\x87partial")
        analyzer.discontinuity()
        analyzer.observe(b"\x7c\x47rest")
        state = analyzer.snapshot()
        self.assertEqual(state["sps_count"], 0)
        self.assertEqual(state["fragment_resets"], 2)

    def test_fragmented_idr_is_counted_once_without_buffering_payload(self):
        analyzer = H264RtpAnalyzer(max_fragment_bytes=1)
        analyzer.observe(b"\x7c\x85large-start", now_ns=9_000_000)
        analyzer.observe(b"\x7c\x05large-middle")
        analyzer.observe(b"\x7c\x45large-end", now_ns=9_000_000)
        state = analyzer.snapshot(now_ns=10_000_000)
        self.assertEqual(state["idr_count"], 1)
        self.assertEqual(state["fragment_resets"], 0)
        self.assertEqual(state["last_idr_age_ms"], 1.0)


if __name__ == "__main__":
    unittest.main()
