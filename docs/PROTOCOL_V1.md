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

The compact Android `health` payload contains only sender-side counters: Primary/Secondary callback and callback-drop counts, emitted RTP packets and access units per feed, telemetry queue drops, telemetry socket errors, video socket errors, and compact callback rates. It is pre-network evidence; it does not replace Edge packet, AU, gap, FPS, or latency metrics.

## Canonical video feeds

Only `primary` and `fpv` are valid configured feed names. Physical DJI sources such as `FPV_CAM`, `LEFT_CAM` and `RIGHT_CAM` remain metadata and must not be confused with the logical feed.

RTP is RFC 3550/RFC 6184 H.264, payload type 96 by default, 90 kHz clock, one SSRC and sequence space per logical feed.

## Access-unit source-time contract

Each `video_au` identifies one completed Android H.264 access unit with
`session`, logical `feed`, `frame_seq`, `rtp_ssrc`, `rtp_ts`,
`au_first_byte_rx_mono_ns`, and `au_complete_rx_mono_ns`. Both monotonic values
are Android `elapsedRealtimeNanos()` observations and are immutable source data:
the Edge must preserve them and use the AU completion time to select telemetry
for that frame.

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
| `5610` | Android → Edge | FPV H.264/RTP, PT `96` |

`clock_ping` requires positive `t0_edge_send_mono_ns`. `clock_pong` additionally requires positive `t1_android_rx_mono_ns` and `t2_android_tx_mono_ns >= t1_android_rx_mono_ns`.

For an Edge-initiated exchange, the Edge sends `clock_ping` to the tablet UDP `5502` from a temporary IPv4 source port. Android replies to that exact source IP and port. The Edge receives that response on the same temporary socket, then records `t3_edge_receive_mono_ns` locally. The fixed Edge UDP `5502` listener is reserved for an Android-initiated `clock_ping`; it is not the return path for an Edge-initiated exchange.

Clock mapping estimates Android-minus-edge monotonic offset. It does not manufacture camera exposure time or a DJI source timestamp.
