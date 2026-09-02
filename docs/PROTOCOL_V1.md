# Matrice transport protocol v1

## Canonical telemetry form

The canonical Android wire form is fragmented `data.fields`, not compact top-level telemetry. Compact packets remain accepted only for compatibility and protocol tests.

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
    "sample_sequence": 8,
    "component_index": 0,
    "chunk_index": 0,
    "chunk_count": 2,
    "fields": {
      "aircraft.latitude_deg": {
        "value": 38.0,
        "valid": true,
        "source": "flight",
        "component_index": 0
      }
    }
  }
}
```

Rules:

- maximum datagram size defaults to 1200 bytes;
- `seq` is monotonic per `(session,type,stream)` datagram;
- `sample_sequence` identifies one native DJI callback across all its chunks;
- a sample is published only after every chunk arrives;
- field names carry units as suffixes, such as `_deg`, `_m`, `_m_s` and `_ns`;
- every field carries explicit validity and source/component identity;
- `rx_mono_ns` is Android `elapsedRealtimeNanos()` callback arrival time.

## Canonical video feeds

Only `primary` and `secondary` are valid configured feed names. Physical DJI sources such as `FPV_CAM`, `LEFT_CAM` and `RIGHT_CAM` remain metadata and must not be confused with the logical feed.

RTP is RFC 3550/RFC 6184 H.264, payload type 96 by default, 90 kHz clock, one SSRC and sequence space per logical feed.

## Clock packets

`clock_ping` requires positive `t0_edge_send_mono_ns`. `clock_pong` additionally requires positive `t1_android_rx_mono_ns` and `t2_android_tx_mono_ns >= t1_android_rx_mono_ns`.

Clock mapping estimates Android-minus-edge monotonic offset. It does not manufacture camera exposure time or a DJI source timestamp.
