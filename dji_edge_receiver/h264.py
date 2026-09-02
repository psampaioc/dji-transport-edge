from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import time


class H264ParseError(ValueError):
    pass


class _Bits:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0

    def read(self, count: int) -> int:
        if count < 0 or self.position + count > len(self.data) * 8:
            raise H264ParseError("truncated SPS")
        value = 0
        for _ in range(count):
            byte = self.data[self.position // 8]
            value = (value << 1) | ((byte >> (7 - self.position % 8)) & 1)
            self.position += 1
        return value

    def ue(self) -> int:
        zeros = 0
        while self.read(1) == 0:
            zeros += 1
            if zeros > 31:
                raise H264ParseError("invalid Exp-Golomb value")
        return (1 << zeros) - 1 + (self.read(zeros) if zeros else 0)

    def se(self) -> int:
        value = self.ue()
        return (value + 1) // 2 if value & 1 else -(value // 2)


def _rbsp(data: bytes) -> bytes:
    output = bytearray()
    zeros = 0
    for value in data:
        if zeros >= 2 and value == 0x03:
            zeros = 0
            continue
        output.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return bytes(output)


def _skip_scaling_list(bits: _Bits, size: int) -> None:
    last = 8
    next_value = 8
    for _ in range(size):
        if next_value:
            next_value = (last + bits.se() + 256) % 256
        last = next_value or last


def parse_sps_dimensions(nal: bytes) -> dict:
    """Return coded display dimensions from one complete SPS NAL unit."""
    if not nal or nal[0] & 0x1F != 7:
        raise H264ParseError("NAL is not SPS")
    raw = _rbsp(nal[1:])
    bits = _Bits(raw)
    profile_idc = bits.read(8)
    bits.read(8)  # constraints and reserved bits
    level_idc = bits.read(8)
    bits.ue()  # seq_parameter_set_id
    chroma_format_idc = 1
    separate_colour_plane_flag = 0
    if profile_idc in {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}:
        chroma_format_idc = bits.ue()
        if chroma_format_idc == 3:
            separate_colour_plane_flag = bits.read(1)
        bits.ue()  # bit_depth_luma_minus8
        bits.ue()  # bit_depth_chroma_minus8
        bits.read(1)  # qpprime_y_zero_transform_bypass_flag
        if bits.read(1):
            count = 8 if chroma_format_idc != 3 else 12
            for index in range(count):
                if bits.read(1):
                    _skip_scaling_list(bits, 16 if index < 6 else 64)
    bits.ue()  # log2_max_frame_num_minus4
    pic_order_cnt_type = bits.ue()
    if pic_order_cnt_type == 0:
        bits.ue()
    elif pic_order_cnt_type == 1:
        bits.read(1)
        bits.se()
        bits.se()
        for _ in range(bits.ue()):
            bits.se()
    bits.ue()  # max_num_ref_frames
    bits.read(1)  # gaps_in_frame_num_value_allowed_flag
    width_mbs = bits.ue() + 1
    height_map_units = bits.ue() + 1
    frame_mbs_only_flag = bits.read(1)
    if not frame_mbs_only_flag:
        bits.read(1)  # mb_adaptive_frame_field_flag
    bits.read(1)  # direct_8x8_inference_flag
    crop_left = crop_right = crop_top = crop_bottom = 0
    if bits.read(1):
        crop_left = bits.ue()
        crop_right = bits.ue()
        crop_top = bits.ue()
        crop_bottom = bits.ue()

    chroma_array_type = 0 if separate_colour_plane_flag else chroma_format_idc
    sub_width = 1 if chroma_array_type in {0, 3} else 2
    sub_height = 2 if chroma_array_type == 1 else 1
    crop_unit_x = 1 if chroma_array_type == 0 else sub_width
    crop_unit_y = (2 - frame_mbs_only_flag) * (1 if chroma_array_type == 0 else sub_height)
    width = width_mbs * 16 - (crop_left + crop_right) * crop_unit_x
    height = height_map_units * 16 * (2 - frame_mbs_only_flag) \
        - (crop_top + crop_bottom) * crop_unit_y
    if width <= 0 or height <= 0:
        raise H264ParseError("SPS produced invalid dimensions")
    return {
        "width": width,
        "height": height,
        "profile_idc": profile_idc,
        "level_idc": level_idc,
        "chroma_format_idc": chroma_format_idc,
    }


@dataclass
class _Fragment:
    nal_type: int
    data: bytearray | None


class H264RtpAnalyzer:
    """Bounded, passive RFC 6184 metadata inspection; never changes relay bytes."""

    def __init__(self, max_fragment_bytes: int = 1_048_576) -> None:
        self.max_fragment_bytes = max_fragment_bytes
        self.nal_counts: Counter[int] = Counter()
        self.sps_count = 0
        self.pps_count = 0
        self.idr_count = 0
        self.sps_parse_errors = 0
        self.fragment_resets = 0
        self.stream_info: dict | None = None
        self.last_idr_mono_ns: int | None = None
        self._fragment: _Fragment | None = None

    def observe(self, payload: bytes, now_ns: int | None = None) -> None:
        if not payload:
            return
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        nal_type = payload[0] & 0x1F
        if 1 <= nal_type <= 23:
            self._observe_nal(payload, now_ns)
            return
        if nal_type == 24:  # STAP-A
            offset = 1
            while offset + 2 <= len(payload):
                size = int.from_bytes(payload[offset:offset + 2], "big")
                offset += 2
                if size == 0 or offset + size > len(payload):
                    self.fragment_resets += 1
                    return
                self._observe_nal(payload[offset:offset + size], now_ns)
                offset += size
            return
        if nal_type != 28 or len(payload) < 2:  # only FU-A is emitted by Android v1
            self.nal_counts[nal_type] += 1
            return
        start = bool(payload[1] & 0x80)
        end = bool(payload[1] & 0x40)
        reconstructed_type = payload[1] & 0x1F
        if start:
            if self._fragment is not None:
                self.fragment_resets += 1
            if reconstructed_type == 7:
                header = bytes([(payload[0] & 0xE0) | reconstructed_type])
                data = bytearray(header + payload[2:])
            else:
                # Count non-SPS fragmented NALs once, but do not copy their
                # potentially large video payload. Resolution is the only
                # metric that requires complete NAL contents.
                data = None
                self._observe_nal(bytes([reconstructed_type]), now_ns)
            self._fragment = _Fragment(reconstructed_type, data)
            if end:
                self._finish_fragment(now_ns)
            return
        if self._fragment is None or self._fragment.nal_type != reconstructed_type:
            self.fragment_resets += 1
            self._fragment = None
            return
        if self._fragment.data is not None:
            self._fragment.data.extend(payload[2:])
            if len(self._fragment.data) > self.max_fragment_bytes:
                self.fragment_resets += 1
                self._fragment = None
                return
        if end:
            self._finish_fragment(now_ns)

    def discontinuity(self) -> None:
        if self._fragment is not None:
            self.fragment_resets += 1
            self._fragment = None

    def _finish_fragment(self, now_ns: int) -> None:
        assert self._fragment is not None
        data = None if self._fragment.data is None else bytes(self._fragment.data)
        self._fragment = None
        if data is not None:
            self._observe_nal(data, now_ns)

    def _observe_nal(self, nal: bytes, now_ns: int) -> None:
        if not nal:
            return
        nal_type = nal[0] & 0x1F
        self.nal_counts[nal_type] += 1
        if nal_type == 7:
            self.sps_count += 1
            try:
                self.stream_info = parse_sps_dimensions(nal)
            except H264ParseError:
                self.sps_parse_errors += 1
        elif nal_type == 8:
            self.pps_count += 1
        elif nal_type == 5:
            self.idr_count += 1
            self.last_idr_mono_ns = now_ns

    def snapshot(self, now_ns: int | None = None) -> dict:
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        return {
            "stream_info": dict(self.stream_info) if self.stream_info else None,
            "nal_counts": {str(key): value for key, value in sorted(self.nal_counts.items())},
            "sps_count": self.sps_count,
            "pps_count": self.pps_count,
            "idr_count": self.idr_count,
            "sps_parse_errors": self.sps_parse_errors,
            "fragment_resets": self.fragment_resets,
            "last_idr_age_ms": None if self.last_idr_mono_ns is None else round(
                (now_ns - self.last_idr_mono_ns) / 1_000_000, 3
            ),
        }
