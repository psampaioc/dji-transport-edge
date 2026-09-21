# Matrice transport protocol v1

## Compact telemetry form

The canonical Android wire form is one compact `data.fields` datagram per callback. It is the current Android implementation, not a proposed richer schema. The Edge must accept it without requiring fragmentation, `sample_sequence`, `component_index`, or per-field source metadata.

Each datagram contains:

```json
{
  "v": 1,
  "type": "flight",
  "session": "session-id",
  "stream": "flight:0",
  "seq": 42,
  "rx_mono_ns": 752340000000,
  "data": {
    "fields": {
      "aircraft.latitude_deg": {
        "value": 38.0,
        "valid": true
      }
    }
  }
}
```

Rules:

- maximum datagram size defaults to 1200 bytes;
- `seq` is monotonic per `(session,type,stream)` datagram;
- field names carry units as suffixes, such as `_deg`, `_m`, `_m_s` and `_ns`;
- field validity is carried when the Android source provides it; the telemetry type and stream identify the producer;
- `rx_mono_ns` is Android `elapsedRealtimeNanos()` callback arrival time.

Only these telemetry fields belong in the transport contract:

- `flight`: `aircraft.latitude_deg`, `aircraft.longitude_deg`, `aircraft.altitude_m`, `heading_deg`;
- `rtk`: `fusion.latitude_deg`, `fusion.longitude_deg`, `is_being_used`;
- `gimbal`: `attitude.pitch_deg`.

`health` and `video_au` stay compact diagnostic packets. Battery, parser dumps, raw video bytes, and unrelated DJI telemetry do not belong in normal transport packets.

`health` is a separate sender-only packet on port `5500`. It carries exactly these unsigned counters: `primary_video_callbacks`, `primary_video_callback_drops`, `primary_rtp_packets`, `primary_access_units`, `telemetry_queue_drops`, `telemetry_socket_errors`, and `video_socket_errors`. Its envelope is `type: "health"`, `stream: "health:0"`, a session, monotonic `seq`, and Android `rx_mono_ns`; its `data` object contains those counters directly, not `data.fields`.

Health proves Android-local progress only. It cannot update navigation, gimbal state, frame context, or prove that a specific Android send reached Ubuntu.

## Canonical video feed

Only `primary` is accepted by this Edge transport. Physical DJI sources such as `FPV_CAM`, `LEFT_CAM` and `RIGHT_CAM` remain Android-side metadata and must not be confused with the one logical feed.

RTP is RFC 3550/RFC 6184 H.264, payload type 96 by default, 90 kHz clock, one SSRC and sequence space per logical feed.

## Access-unit source-time contract

Each `video_au` identifies one completed Android H.264 access unit with
`session`, logical `feed`, `frame_seq`, `rtp_ssrc`, `rtp_ts`,
`au_first_byte_rx_mono_ns`, and `au_complete_rx_mono_ns`. Both monotonic values
are Android `elapsedRealtimeNanos()` observations and are immutable source data:
the Edge must preserve them as source metadata. The current Edge transport does
not associate an AU to a decoded ROS image and therefore does not use this data
to select navigation for an image.

`dji_source_timestamp_ns` is optional. When present it **must** be accompanied
by `dji_timestamp_source`, which names the documented DJI callback/API clock.
It is retained as separate source metadata; it never replaces Android monotonic
time unless a later, explicit contract says so.

Edge receive, decode, and ROS-delivery times are separate diagnostic
observations. They may measure transport latency but are never frame identity,
camera time, or a telemetry correlation key.

## UDP ports and clock packets

| UDP port | Direction | Payload |
| --- | --- | --- |
| `5500` | Android → Edge | compact telemetry and health JSON |
| `5501` | Android → Edge | `video_au` JSON metadata |
| `5502` | bidirectional | clock request/reply JSON |
| `5600` | Android → Edge | Primary H.264/RTP, PT `96` |

`clock_ping` requires positive `t0_edge_send_mono_ns`. `clock_pong` additionally requires positive `t1_android_rx_mono_ns` and `t2_android_tx_mono_ns >= t1_android_rx_mono_ns`.

For an Edge-initiated exchange, the Edge sends `clock_ping` to the tablet UDP `5502` from a temporary IPv4 source port. Android replies to that exact source IP and port. The Edge receives that response on the same temporary socket, then records `t3_edge_receive_mono_ns` locally. The fixed Edge UDP `5502` listener is reserved for an Android-initiated `clock_ping`; it is not the return path for an Edge-initiated exchange.

Clock mapping estimates Android-minus-edge monotonic offset. It does not manufacture camera exposure time or a DJI source timestamp.

## Edge-side video health

The following are Edge observations, not additions to the Android wire
contract. The current Primary transport keeps one GStreamer decoder and a
one-item newest-frame handoff. It records separate ages for the last RTP
datagram, decoded image and ROS publication. When RTP remains fresh while
decoded progress stops, it reports the waiting boundary and waits for a future
sender IDR; it does not recreate the decoder automatically. Telemetry,
navigation history and all Android/DJI source-time fields remain untouched.

`TransportStatus.state` distinguishes `WAITING_FOR_UDP`,
`WAITING_FOR_KEYFRAME`, `DECODING` (fresh RTP but no current decoded image),
`DELIVERY_STALLED`, `PUBLISHING`, and `PIPELINE_ERROR`. Its human-readable
`state_reason` locates the first directly observed boundary and must not be
interpreted as a new packet type or source timestamp.

`primary_stale_after_s` defaults to `1.0` seconds. It is the age at which an
otherwise active observation becomes stale. `sender_correlation_s` defaults to
`2.0` seconds and limits the diagnostic-only comparison of fresh Android health
or metadata with stale Edge RTP. Increasing either value delays diagnosis; it
does not create buffering or make UDP reliable.

The supported path has no native preview renderer. RViz is a downstream ROS consumer and not part of the video transport path.

## Session and diagnostic evidence

The Edge accepts a new valid Android session as a boundary: it clears telemetry
composition, metadata and incident state before accepting fresh data. A bounded
set of retired session IDs prevents delayed UDP packets from reviving a prior
session. Sequence numbers are monotonic independently for `flight:0`, `rtk:0`,
`gimbal:0`, `health:0`, and Primary `video_au` metadata.

The Edge records one bounded incident on entry to a degraded episode and one
recovery when publishing resumes, in session-scoped NDJSON. It records sender
health, JSON receipt, metadata, RTP ingress, decode, ROS publication and clock
observations, never RTP/H.264 payloads. A fresh Android health packet with stale
Edge RTP is an Android-local-progress / Edge-ingress gap, not proof of Wi-Fi
loss. State changes inside an already-open incident do not create more records.

RTP sequence continuity is reported only after the active `udpsrc` source probe
observes valid complete RTP headers. It does not open a second listener on
`5600`. Any correlation between health, metadata and RTP is diagnostic only;
`FrameContext` remains `ASSOCIATION_UNAVAILABLE` until a separate design proves
AU-to-decoded-image identity.

The decoder backend and its selection reason are also Edge-only diagnostics. `nvidia` is reported only after the active container's GStreamer runtime starts the NVIDIA decoder pipeline; `cpu` means `avdec_h264` is active, with a concrete fallback reason. Neither value changes the Android RTP/H.264 contract or any source timestamp.
