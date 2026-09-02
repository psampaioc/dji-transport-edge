# Matrice edge transport

This is the standalone Ubuntu-side transport project for the Matrice tablet application. It accepts versioned UDP telemetry and frame metadata, validates ordering and timestamps, maps the Android monotonic clock to the Ubuntu monotonic clock, relays valid RTP/H.264 packets unchanged to a low-latency GStreamer pipeline, and records append-only evidence.

The process never controls the aircraft. It has no DJI SDK dependency and no flight-command path.

## Data path

```text
Android telemetry UDP :5500 ----> validation ----> latest state + NDJSON
Android frame sidecar :5501 ----> validation ----> RTP/session index + NDJSON
Android clock pong    :5502 ----> NTP-style mapper + clock.ndjson
Android primary RTP  :5600 ----> RTP validation ----> unchanged UDP relay :5602
Android secondary RTP:5610 ----> RTP validation ----> unchanged UDP relay :5612
                                                         |
                                                         v
                                                    GStreamer
                                                         |
                                                         v
                                  pre-ROS validation: decoded frames discarded
```

The RTP relay exists so protocol validation and evidence cannot be bypassed while GStreamer still receives standard RTP. It does not decode, transcode, resize, or otherwise alter H.264 packets. GStreamer owns RTP depayloading and decoding. The default headless pipeline ends in `fakesink`; the local visual bench may use `ximagesink`. The future ROS 2 adapter will replace that tail with in-process `appsink`, avoiding an extra Unix socket and copy. See the [`usage guide`](GUIA_DE_UTILIZACAO.md) and [`roadmap`](docs/ROADMAP.md).

## Install and run

Ubuntu 24.04 packages for the default software-decoding pipeline:

```bash
sudo apt install python3-venv gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.toml config.toml
.venv/bin/dji-edge-receiver dashboard --config config.toml
```

`dashboard` opens the localhost measurement panel plus the configured native GStreamer windows. It shows independent Primary/Secondary health, video and telemetry counters, clock quality, Ubuntu IPv4 addresses, and can safely update the tablet clock IP through **Save and restart receiver**. It writes dashboard bench reports under `.runtime/bench/`. `run` remains the headless/systemd command. Run from this repository root, or use absolute paths. `config.toml` resolves a relative `evidence_dir` relative to the config file. The included systemd unit is a deployment template; review its paths before installing it.

### Global application command

Like `detection-verification`, install the project with `uv` to use it from any directory:

```bash
uv tool install --force /home/psampaioc/Workspaces/edge-matrice-transport
dji-edge init-config --from /home/psampaioc/Workspaces/edge-matrice-transport/config.toml
dji-edge
```

The initial copy creates `~/.config/dji-edge-receiver/config.toml` and rebases its relative evidence path to the existing evidence directory. The full command name, `dji-edge-receiver`, remains available too. With no subcommand, both names open the dashboard. A command-line `--config` has priority, followed by `DJI_EDGE_RECEIVER_CONFIG`, a local `./config.toml`, and the per-user configuration.

See the [complete command and flag reference](GUIA_DE_UTILIZACAO.md#referência-completa-de-comandos-e-flags) for `dashboard`, headless `run`, pipeline printing, packet validation, and persistent configuration setup.

Useful checks:

```bash
curl http://127.0.0.1:8088/health
curl http://127.0.0.1:8088/v1/state | python3 -m json.tool
.venv/bin/dji-edge-receiver print-gstreamer --config config.toml
.venv/bin/dji-edge-receiver validate-packet telemetry.json
```

For a props-off evidence run after transport is started on the tablet:

```bash
mkdir -p .runtime/bench
.venv/bin/python scripts/capture_transport_bench.py --duration-s 60 \
  --out .runtime/bench/transport-$(date -u +%Y%m%dT%H%M%SZ).json
```

The report records interval deltas for actual DJI callbacks, sender drops/errors, receiver RTP loss, kernel UDP drops, clock-offset quality, frame presence, and causal frame-to-telemetry associations. Receiver health also passively inspects H.264 SPS/PPS/IDR NAL units to report the encoded resolution and keyframe freshness without changing the relayed RTP bytes. RTP access units are not claimed as decoded frames; decoded-frame accounting belongs at the future GStreamer `appsink`. The script refuses to overwrite an existing report.

For NVIDIA/Intel hardware decoding, replace `avdec_h264` in `sink` with the decoder available on the target (`nvv4l2decoder`, `nvh264dec`, or `vah264dec`) after checking `gst-inspect-1.0`. Keep the one-buffer leaky queue and `sync=false` at the detector boundary. No encoder belongs in this pipeline.

## UDP v1 contract

All JSON datagrams must be UTF-8 objects, no larger than `max_datagram_bytes` (default 1200), with:

- `v`: integer protocol version, currently `1`;
- `type`: `flight`, `rtk`, `gimbal`, `health`, `hello`, `telemetry`, `video_au`, `frame_meta`, `clock_ping`, or `clock_pong`;
- `session`: non-empty Android transport-session ID;
- `stream`: stable source ID. `feed` or `source` is accepted as an alias;
- `seq`: monotonically increasing per `(session,type,stream)`. Frame sidecars may use `frame_seq`;
- `rx_mono_ns` or `android_mono_ns`: Android `elapsedRealtimeNanos()` receive time.

The canonical Android form is fragmented `data.fields`; compact top-level telemetry remains accepted only for compatibility. The sender publishes each native source callback independently and fragments a large sample below 1,200 bytes with `sample_sequence`, `chunk_index`, and `chunk_count`. The edge publishes a new latest sample only after all chunks arrive; a newer sample discards an incomplete older sample. See [`docs/PROTOCOL_V1.md`](docs/PROTOCOL_V1.md).

Legacy-compatible compact example (accepted by the receiver, but not canonical for new senders):

```json
{"v":1,"type":"flight","session":"bench-01","stream":"aircraft","seq":42,"rx_mono_ns":752340000000,"valid":true,"data":{"lat":38.0,"lon":-9.0,"alt_m":12.5,"velocity_mps":[0.0,0.0,0.0],"heading_deg":90.0,"flight_mode":"GPS_ATTI","is_flying":false}}
```

Use separate `flight`, `rtk`, and per-component `gimbal` streams. The HTTP state merges the latest complete sources into one comprehensive snapshot and reports freshness. Native cadences remain independent; video is never throttled to telemetry Hz.

Frame sidecars bind the RTP SSRC to the Android session and carry the shared Android monotonic timestamp:

```json
{"v":1,"type":"video_au","session":"bench-01","feed":"primary","physical_source":"LEFT_CAM","frame_seq":301,"rtp_ssrc":305419896,"rtp_ts":900900,"au_first_byte_rx_mono_ns":752341000000,"au_complete_rx_mono_ns":752341400000,"first_rtp_seq":1000,"final_rtp_seq":1018,"idr":false,"bytes":21345,"packets":19}
```

The receiver rejects an unsupported version, malformed fields, invalid frame intervals, wrong endpoint packet types, malformed RTP v2 headers, or wrong RTP payload type. Duplicates, out-of-order packets, gaps, and monotonic-timestamp regressions are recorded explicitly. Duplicates/out-of-order/regressing records remain evidence but cannot replace latest state.

RTP must be RFC 3550/RFC 6184 H.264 with a 90 kHz clock. Configure one SSRC and RTP sequence space per feed. The frame sidecar's `rtp_ts` is the join key for later decoded-frame metadata; `au_first_byte_rx_mono_ns` is Android arrival time, not camera exposure time.

## Clock mapping

When `android_clock_host` is configured, Ubuntu sends `clock_ping` packets once per configured interval. Android returns the same `session`, `stream`, `seq`, and `t0_edge_send_mono_ns`, plus:

```json
{"v":1,"type":"clock_pong","session":"bench-01","stream":"clock","seq":9,"t0_edge_send_mono_ns":1000000,"t1_android_rx_mono_ns":2000100,"t2_android_tx_mono_ns":2000200}
```

Ubuntu captures `t3_edge_receive_mono_ns`. The mapper computes NTP-style round-trip delay and Android-minus-edge offset, then takes the median offset from the lowest-RTT samples. `/v1/state` reports readiness, sample count, offset, and RTT. Until mapping is ready, mapped timestamps and network-age estimates are `null`; the raw Android monotonic times remain authoritative for video/telemetry association.

The live association is causal: the receiver retains a bounded per-source Android-time history and each frame sidecar selects the newest telemetry sample at or before that frame timestamp. A recording/replay adapter can later interpolate bracketing continuous samples without delaying the detector.

## Evidence layout

Each sanitized/hash-suffixed session directory contains:

- `manifest.json`: session ID and evidence format;
- `telemetry.ndjson`: exact raw datagram, parsed object, remote endpoint, receive clocks, and validation result;
- `frame_metadata.ndjson`: exact frame sidecars and validation;
- `clock.ndjson`: accepted clock exchanges and current estimates;
- `video-<stream>.rtpbin`: optional raw RTP records framed as network-order `u64 edge_mono_ns`, `u32 packet_length`, then unchanged packet bytes.

Packets that cannot be assigned safely to a session are written under `_receiver/protocol_errors.ndjson`; RTP received before its sidecar binding uses an explicit `unknown-rtp-session` evidence directory. Disable RTP capture for sustained production runs if storage throughput is not required, but keep telemetry and metadata evidence enabled.

## ROS/detection boundary

`LatestState.snapshot()` and `GET /v1/state` are the stable non-ROS state boundary. The containerized Humble adapter in [`ros2_ws/`](ros2_ws/README.md) consumes that state boundary and the validated loopback RTP relays through GStreamer `appsink`s. It does not own the Android UDP sockets or block ingest. Video QoS remains bounded/best-effort; navigation preserves session, Android/edge monotonic time, validity and position-source identity.

## Tests

Tests use only loopback UDP and synthetic packets:

```bash
python3 -m unittest discover -s tests -v
```

They cover packet/RTP validation, sequence wrap and gaps, clock offset/RTT mapping, latest-state behavior, raw evidence, HTTP state exposure, frame sidecars, and byte-identical RTP relay. GStreamer is disabled in integration tests so they do not require a decoder or camera.
