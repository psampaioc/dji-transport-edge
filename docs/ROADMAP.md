# Edge transport roadmap

## Objective

Move original DJI H.264 and timestamped telemetry from the Android tablet to Ubuntu with the lowest practical latency, bounded memory, and reliable loss recovery. Keep each stage simple enough to measure and restart independently.

## Fixed principles

- Android remains the DJI/MSDK acquisition endpoint; Ubuntu never controls the aircraft.
- Video remains H.264 over RTP/UDP. Do not decode or re-encode on Android.
- GStreamer owns RTP depayloading and decode. Python remains responsible for UDP validation, state, clock mapping, evidence, and orchestration.
- Every live queue is bounded and drops stale video instead of accumulating latency.
- Camera exposure time is unknown. Android callback arrival time is never labelled as exposure time.
- ROS 2 is downstream of a verified non-ROS transport path.

## Ordered corrective work

### 1. Freeze the project boundary — completed

Done in this move: edge receiver, deployment templates, protocol fixtures, and edge scripts live here. The Android/Matrice project retains the DJI sender and shared end-to-end specification.

Acceptance: this repository tests and verifies itself without imports or relative paths into the Android repository.

### 2. Make feed names canonical — completed in source

Resolved discrepancy: the former Python default was `fpv`; Android and deployment configuration use `primary` and `secondary`.

Completed action: removed `fpv` as the default runtime stream, required explicit `primary`/`secondary` configuration, and rejected unknown sidecar feed names.

Acceptance: one loopback test covers both configured feeds, and no default configuration can silently receive a mismatched Android feed.

### 3. Freeze one telemetry wire schema — completed for v1 documentation

Current discrepancy: examples show compact top-level fields while Android sends fragmented `data.fields` with hierarchical names.

Action: document `data.fields` as the v1 canonical wire form; either remove compact examples or mark them legacy-compatible. Specify units, validity, source/index, sequence, and timestamps for every field class.

Acceptance: golden packets are generated from the documented schema and both Android and edge tests validate them.

### 4. Harden packet and clock validation — completed in source

Current discrepancy: malformed `clock_ping` can raise an uncaught `KeyError`; endpoint/port validation is incomplete.

Action: validate every clock timestamp in `decode_json_packet`; validate port ranges, unique input/output ports, payload limits, and positive clock interval before sockets start.

Acceptance: malformed packets and invalid TOML fail deterministically without stopping a receiver thread.

### 5. Make failure observable — implemented for evidence and decoder process health

Current discrepancy: evidence-writer failure and a GStreamer process death are not surfaced as explicit receiver health states.

Action: add health counters/state for evidence I/O failure, evidence drops, decoder process exit, relay send errors, and last-decoded-frame age.

Acceptance: `/v1/state` reports degraded/unavailable status, and a test kills the decoder without killing ingest.

### 6. Decide and implement the final decode boundary — decision complete, ROS implementation pending

Decision: do not implement RFC 6184 depayloading in Python. GStreamer already implements RTP ordering, jitter buffering, H.264 depayloading, parser recovery, and hardware decoders; duplicating that in Python would add maintenance and failure modes without lowering latency.

Action: keep the current external `gst-launch` path as a non-ROS validation tool, then add a small in-process GStreamer pipeline ending in `appsink` inside the future ROS adapter. Keep `rtpjitterbuffer` low and measured, `drop-on-latency=true`, and a one-frame leaky hand-off.

Acceptance: one process owns GStreamer decode and ROS image publication; no `shmsink` or `shmsrc` dependency remains in the live path.

### 7. Benchmark the non-ROS transport on real hardware — instrumentation implemented, long run pending

Action: props-off test primary first, then secondary and dual feed. Record video FPS, IDR recovery time, RTP loss, jitter, Android/edge clock quality, telemetry freshness, restarts, and sustained queue depth.

Implemented edge instrumentation: per-window sender/receiver deltas, passive SPS/PPS/IDR inspection with encoded dimensions and IDR age, Linux UDP socket-drop counter, actual receive-buffer size, RTP access-unit rate/size, telemetry sample deltas, and clock state. This deliberately does not claim decoded-frame counts; those require the step 8 `appsink` boundary. Physical 30-minute, FPV, RTK-valid, restart, and recovery measurements remain to be executed.

Acceptance: all queues remain bounded for 30 minutes and latency/loss results are recorded as evidence, not assumed.

### 8. Add the ROS 2 adapter only after step 7

Action: publish decoded `sensor_msgs/Image` as `bgr8` with timestamps from sidecar/RTP mapping; publish telemetry/RTK/gimbal separately; use best-effort bounded QoS for video and ensure ROS subscribers cannot block ingest or decode.

Acceptance: slow ROS consumers cause dropped stale frames only, never a growing UDP/decode backlog.

### 9. Secure the field-network deployment

Current discrepancy: UDP and HTTP state have no sender authentication or allowlist.

Action: first restrict the receiver to the dedicated private interface/firewall; then decide whether authenticated packets are needed for the actual operating environment.

Acceptance: no untrusted network can inject or expose flight telemetry in the intended deployment.
