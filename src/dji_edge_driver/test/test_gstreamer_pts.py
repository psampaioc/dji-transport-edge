"""Characterize the only allowed internal RTP-to-decoded-frame binding path."""

import time

import pytest

from dji_edge_transport_core.protocol import parse_rtp_packet


gi = pytest.importorskip("gi")
gi.require_version("Gst", "1.0")
from gi.repository import Gst


Gst.init(None)


pytestmark = pytest.mark.skipif(
    Gst.ElementFactory.find("x264enc") is None,
    reason="container lacks x264enc; characterize against props-off RTP evidence instead",
)


def _collect_encoded_rtp():
    pipeline = Gst.parse_launch(
        "videotestsrc num-buffers=12 ! video/x-raw,framerate=30/1 ! "
        "x264enc tune=zerolatency speed-preset=ultrafast key-int-max=6 ! "
        "rtph264pay pt=96 config-interval=1 ! appsink name=sink sync=false"
    )
    sink = pipeline.get_by_name("sink")
    pipeline.set_state(Gst.State.PLAYING)
    packets = []
    try:
        while True:
            sample = sink.emit("try-pull-sample", Gst.SECOND)
            if sample is None:
                break
            packets.append(sample.get_buffer().copy())
    finally:
        pipeline.set_state(Gst.State.NULL)
    assert packets
    return packets


def test_post_jitter_rtp_pts_reaches_decoded_h264_access_unit():
    packets = _collect_encoded_rtp()
    pipeline = Gst.parse_launch(
        "appsrc name=source is-live=false format=time "
        "caps=application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload=96 ! "
        "rtpjitterbuffer name=jitter latency=0 drop-on-latency=true ! rtph264depay ! h264parse ! "
        "avdec_h264 ! videoconvert ! video/x-raw,format=BGR ! appsink name=sink sync=false"
    )
    source, jitter, sink = (pipeline.get_by_name(name) for name in ("source", "jitter", "sink"))
    post_jitter = {}

    def observe(_pad, info):
        buffer = info.get_buffer()
        if buffer is not None and buffer.pts != Gst.CLOCK_TIME_NONE:
            packet = parse_rtp_packet(buffer.extract_dup(0, buffer.get_size()), 96)
            post_jitter[(packet.ssrc, packet.timestamp)] = int(buffer.pts)
        return Gst.PadProbeReturn.OK

    jitter.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, observe)
    pipeline.set_state(Gst.State.PLAYING)
    decoded_pts = []
    try:
        for packet in packets:
            # UDP input has no meaningful source PTS; jitterbuffer reconstructs
            # an internal PTS from RTP sequence/timestamp.
            packet.pts = Gst.CLOCK_TIME_NONE
            packet.dts = Gst.CLOCK_TIME_NONE
            assert source.emit("push-buffer", packet) == Gst.FlowReturn.OK
        source.emit("end-of-stream")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            sample = sink.emit("try-pull-sample", 100 * Gst.MSECOND)
            if sample is not None and sample.get_buffer().pts != Gst.CLOCK_TIME_NONE:
                decoded_pts.append(int(sample.get_buffer().pts))
            message = pipeline.get_bus().timed_pop_filtered(0, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            if message is not None:
                assert message.type != Gst.MessageType.ERROR, message.parse_error()
                if message.type == Gst.MessageType.EOS:
                    break
    finally:
        pipeline.set_state(Gst.State.NULL)
    assert post_jitter, "rtpjitterbuffer did not provide usable internal PTS"
    assert decoded_pts, "decoder did not emit a usable internal PTS"
    assert set(decoded_pts) & set(post_jitter.values()), "decoder PTS did not match post-jitter RTP PTS"
