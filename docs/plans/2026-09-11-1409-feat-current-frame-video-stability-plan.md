---
title: "feat: Stabilize current-frame Primary video"
type: feat
date: 2026-09-11
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# feat: Stabilize current-frame Primary video

## Goal Capsule

- **Objective:** Deliver the newest decodable Primary image continuously to ROS/RViz without Edge-created backlog, automatic decoder restarts, or unexplained freezes.
- **Means:** Keep the direct Android UDP/RTP → GStreamer → one latest-frame handoff → ROS image route; remove nonessential hot-path work and expose the first boundary that stops progressing.
- **Authority:** Android owns H.264/RTP and Android/DJI timestamps. `docs/PROTOCOL_V1.md` owns the wire contract.
- **Execution profile:** Characterization-first in the project Humble container, then a props-off tablet bench. Android source tuning follows evidence.
- **Stop conditions:** Do not change Android, ports, RTP semantics, source timestamps, mapper/map, flight behavior or FPV. Do not add preview, relay, FFmpeg, `shmsink`, default raw RTP capture, or another ROS node.

---

## Product Contract

### Summary

Primary remains a small, headless route: tablet RTP enters GStreamer, `nvh264dec` is used only if the active container can truly start it, `avdec_h264` is the reliable fallback, and a single current image reaches the existing ROS topic.

This covers the full Primary current-frame stability contract. Navigation, map/path, telemetry and dashboard configuration remain compatible observers. FPV remains deliberately deferred until the Primary route is proven on the real Wi-Fi link.

### Problem Frame

The current C++ route has a one-item `FrameHandoff` and no preview/restart branch, but ROS delivery is awakened by a fixed 16 ms timer and status cannot reliably distinguish ingress loss, H.264 recovery, decode-to-ROS delay, or an RViz-only symptom. This makes periodic freezes impossible to act on with confidence.

### Key Decisions

- KD1. **Newest image wins over buffered continuity.** `(session-settled: user-directed — chosen over accepting extra buffering: field operation needs the most current scene.)` Governs R1, R2, R5.
- KD2. **Keep the direct ROS route.** `(session-settled: user-approved — chosen over FFmpeg, relay, re-encode, shared memory, or a second preview consumer: the GStreamer/NVIDIA route is the smallest reliable path into standard ROS images.)` Governs R1, R3, R6.
- KD3. **Tune the source only from measured evidence.** `(session-settled: user-approved — chosen over immediately forcing 20 FPS: the link must prove 30 FPS is the limiting factor before quality is reduced.)` Governs R4, R5.

### Requirements

- R1. Primary retains only the newest decoded image awaiting ROS publication; it never builds a multi-frame queue or publishes historical imagery for apparent continuity.
- R2. Fresh decoded imagery reaches ROS by a bounded event-driven handoff, not an arbitrary polling cadence that can reduce usable source rate.
- R3. Primary uses one independent headless GStreamer pipeline with runtime-verified NVIDIA decoding and a truthful CPU fallback.
- R4. Compact status distinguishes fresh RTP, H.264/keyframe recovery, decoder output, ROS publication and renderer-only symptoms without copying or retaining RTP payloads for normal metrics.
- R5. The operator view exposes ingress bitrate, decoded/published FPS, latest-image age, superseded-frame rate, resolution, active decoder and pipeline error.
- R6. Preserve Android ports, RTP/H.264, original Android/DJI timestamps, navigation/map/path, dashboard local configuration and `/dji/primary/image_raw`.
- R7. FPV, re-encoding, raw RTP by default, a second viewer, GPU-native ROS transport, mapper changes and Android source changes are out of scope.

### Key Flows

- F1. **Healthy delivery:** valid Primary RTP is decoded, replaces any pending older frame and publishes the newest image. RViz/downstream consumers see current imagery. Covers R1–R3.
- F2. **Visible freeze:** status identifies whether ingress stopped, H.264 awaits an IDR, delivery stalled, or ROS is fresh but RViz is stale. The operator gets one actionable boundary. Covers R4–R5.

### Acceptance Examples

- AE1. **Current image over queue:** when two images decode before the next ROS delivery, only the newer image is delivered; the older one is counted as superseded. Covers R1.
- AE2. **Keyframe recovery:** when RTP remains recent but H.264 is undecodable after loss, the next Android IDR resumes the existing pipeline without a restart. Covers R3–R4.
- AE3. **Source-rate decision:** after a props-off 30 FPS bench has stable ingress/decode/publication, 20 FPS is not adopted as a workaround. Covers R5.

### Scope Boundaries

- Do not add display queues, native preview, relay, a second ROS node or payload-level RTP diagnostics.
- Do not modify Android, DJI source selection, telemetry fields, map assets or localization behavior in this repository.
- Edge cannot recover missing UDP; it waits for a future sender IDR.

<!-- ce-section: work-relationships -->
### How This Work Fits Together

`dji_edge_transport` owns Primary delivery. It shares the existing topic with the mapper, gives the dashboard read-only status/config persistence, and depends on Android IDR cadence plus Wi-Fi viability. FPV follows only after Primary equivalence.

### Dependencies and Assumptions

- The project Humble service can expose NVIDIA GStreamer plugins through the NVIDIA container runtime; actual pipeline startup, not host GPU presence, is authoritative.
- `sensor_msgs/Image` needs CPU-addressable bytes. The decoded GStreamer buffer to ROS image path has one unavoidable final CPU copy; GPU-native ROS transport is a separate project.
- The tablet emits Primary continuously and offers an IDR cadence suitable for recovery from real UDP loss.

### Sources and Research

- `src/dji_edge_transport/src/primary_video_pipeline.cpp`
- `src/dji_edge_transport/src/frame_handoff.cpp`
- `src/dji_edge_transport/src/transport_node.cpp`
- `src/dji_edge_transport/config/transport.yaml`
- `docs/PROTOCOL_V1.md`
- `docs/PRIMARY_CPP_BENCH.md`

---

## Planning Contract

### Product Contract Preservation

Product Contract restructured, no scope change: R1–R7, F1–F2 and AE1–AE3 retain their original intent; this section adds implementation decisions and verification only.

### Key Technical Decisions

- KTD1. **One latest-frame handoff has one condition-backed delivery worker.** The streaming callback produces one CPU-owned latest frame and signals a single internal publisher worker. That worker takes the newest available frame and performs ROS publication, so DDS/RViz pressure cannot block decoding or create a second queue. Governs R1, R2.
- KTD2. **No decoder watchdog.** GStreamer errors are reported and H.264 loss waits for the next IDR in the existing pipeline. Restart loops are not a recovery mechanism. Governs R3, R4.
- KTD3. **Metrics are completed-image or periodic-snapshot metrics.** The code counts datagrams/bytes, decoded frames, superseded frames and published frames at their own boundaries; it never clones, stores or parses RTP payloads for ordinary status. Governs R4, R5.
- KTD4. **NVIDIA is runtime-verified.** Report `nvidia` only after the active runtime finds `nvh264dec` and enters PLAYING; otherwise clear the failed graph and use CPU with a concrete reason. Governs R3, R5.
- KTD5. **Dashboard is optional and non-fatal.** It receives an immutable snapshot and writes only the validated clock-target overlay for a later explicit launch. It has no transport lifecycle authority. Governs R4, R6.

### High-Level Technical Design

```mermaid
flowchart LR
  T[Android Primary RTP] --> J[low-latency GStreamer]
  J --> D{runtime decoder}
  D -->|available| N[nvh264dec]
  D -->|fallback| C[avdec_h264]
  N --> A[appsink max 1 drop]
  C --> A
  A --> H[one latest-frame handoff]
  H --> W[coalesced ROS wake-up]
  W --> P[/dji/primary/image_raw QoS depth 1]
  J -. ingress .-> S[compact status]
  A -. decode .-> S
  P -. publish .-> S
  S --> O[dashboard and diagnostics]
```

The application performs one managed image-byte copy when moving the decoded CPU-accessible GStreamer buffer into ROS-image storage; decoder/color-conversion internals may still allocate their required output buffer. The system has no tee, renderer, relay, raw RTP recorder or second video consumer.

### Resolved During Planning

- The 16 ms frame timer is replaced as the normal delivery trigger; it is both a bounded delay and an attribution blind spot.
- Direct ROS publication inside the GStreamer streaming callback is rejected because DDS work may backpressure decode.
- Android bitrate, resolution and FPS remain unchanged until a real bench proves the source/link rather than Edge delivery is limiting.
- No external research is needed: the local protocol and current GStreamer/ROS design are authoritative; the important unknown is live hardware behavior.

### Deferred to Implementation

- Calibrate stale thresholds from the real props-off baseline.

---

## Implementation Units

### U1. Establish compact Primary progress evidence

- **Goal:** Attribute every freeze to ingress, decoder, ROS delivery or rendering without RTP payload-level work.
- **Requirements:** R4, R5, AE2, AE3.
- **Dependencies:** None.
- **Files:** `src/dji_edge_transport/include/dji_edge_transport/primary_video_pipeline.hpp`, `src/dji_edge_transport/src/primary_video_pipeline.cpp`, `src/dji_edge_transport/msg/TransportStatus.msg`, `src/dji_edge_transport/include/dji_edge_transport/transport_node.hpp`, `src/dji_edge_transport/src/transport_node.cpp`, `src/dji_edge_transport/test/test_primary_video_pipeline.cpp`, `src/dji_edge_transport/test/test_transport_node.cpp`, `src/dji_edge_transport/CMakeLists.txt`.
- **Approach:** Count lightweight RTP datagrams/bytes at source, decoded frames at appsink, replacements at handoff and publications at ROS. Compute periodic rates/ages and explicit fresh-RTP/no-decode, no-ingress, delivery-stalled and pipeline-error states. Preserve source timestamps as identity data and label Edge times as diagnostics only.
- **Patterns to follow:** `VideoSnapshot`, `TransportStatus`, `age_seconds`, `FrameHandoff::dropped_old_frames` and `DashboardServer` state snapshots.
- **Test scenarios:**
  - Covers AE2. Fresh RTP/no decoded image reports keyframe recovery, not pipeline failure.
  - Decoded/no-published image reports delivery separately from no ingress.
  - Rates use deltas without retaining RTP data.
  - Pipeline errors produce status but no restart request.
  - Invalid or backwards observations never create false ages/rates.
- **Verification:** An operator can classify each boundary from compact status; no callback copies or parses RTP payloads.

### U2. Replace fixed polling with bounded event-driven delivery

- **Goal:** Promptly publish the newest image without a queue or DDS work in the GStreamer streaming thread.
- **Requirements:** R1, R2, R6, AE1.
- **Dependencies:** U1.
- **Files:** `src/dji_edge_transport/include/dji_edge_transport/frame_handoff.hpp`, `src/dji_edge_transport/src/frame_handoff.cpp`, `src/dji_edge_transport/include/dji_edge_transport/primary_video_pipeline.hpp`, `src/dji_edge_transport/src/primary_video_pipeline.cpp`, `src/dji_edge_transport/include/dji_edge_transport/transport_node.hpp`, `src/dji_edge_transport/src/transport_node.cpp`, `src/dji_edge_transport/test/test_frame_handoff.cpp`, `src/dji_edge_transport/test/test_primary_video_pipeline.cpp`, `src/dji_edge_transport/test/test_transport_node.cpp`.
- **Approach:** Keep handoff capacity exactly one. A successful replacement signals one condition variable consumed by one internal publisher worker. Repeated decode events only replace the pending frame; the worker takes and moves the newest bytes into `sensor_msgs/Image`. Stop the worker before pipeline/node teardown completes.
- **Execution note:** Add characterization tests before changing this latency-sensitive seam.
- **Test scenarios:**
  - Covers AE1. Two frames before delivery yield one publication of the newer frame and one supersession.
  - One decoded image schedules one delivery without waiting for a periodic frame timer.
  - Repeated wake-ups cannot create an unbounded task/frame queue.
  - Slow or absent subscribers cannot make retained image count exceed one.
  - Shutdown with pending work produces no post-teardown publish.
- **Verification:** Normal Primary delivery has no frame polling timer; the streaming callback remains bounded and ROS receives only current moved image data.

### U3. Harden headless decoder and container truthfulness

- **Goal:** Keep one stable Primary pipeline and state exactly which decoder is active in the current container.
- **Requirements:** R3–R6, AE2.
- **Dependencies:** U1.
- **Files:** `src/dji_edge_transport/include/dji_edge_transport/primary_video_pipeline.hpp`, `src/dji_edge_transport/src/primary_video_pipeline.cpp`, `src/dji_edge_transport/config/transport.yaml`, `src/dji_edge_transport/test/test_primary_video_pipeline.cpp`, `src/dji_edge_transport/test/test_transport_node.cpp`, `README.md`.
- **Approach:** Retain one `appsink max-buffers=1 drop=true` graph. Remove/reject preview, tee, renderer, `fakesink`, raw-capture and watchdog paths in new transport. Validate NVIDIA factory and PLAYING transition, then fall back cleanly to CPU. Verify the already configured project Humble service exposes NVIDIA runtime/device capability when used; retain CPU-only startup. Container-definition changes are outside this repository's edit scope.
- **Test scenarios:**
  - Successful NVIDIA startup reports `nvidia`.
  - Missing factory and NVIDIA start failure report distinct CPU fallback reasons.
  - Pipeline error has no restart loop.
  - Graph has one appsink and no display/tee branch.
  - CPU-only preflight succeeds.
- **Verification:** A live run reports one active decoder/backend truthfully; no host-GPU assumption exists.

### U4. Preserve dashboard as an observer

- **Goal:** Keep an optional local dashboard useful without putting it in the video path.
- **Requirements:** R4–R6.
- **Dependencies:** U1, U2.
- **Files:** `src/dji_edge_transport/include/dji_edge_transport/dashboard_server.hpp`, `src/dji_edge_transport/src/dashboard_server.cpp`, `src/dji_edge_transport/src/transport_node.cpp`, `src/dji_edge_transport/config/transport.yaml`, `src/dji_edge_transport/test/test_dashboard_server.cpp`, `src/dji_edge_transport/test/test_transport_node.cpp`, `README.md`.
- **Approach:** Render the compact progress snapshot through loopback HTTP. Persist only validated clock IPv4 for a later explicit launch. Dashboard bind/HTTP errors are diagnostics only and may not restart/stop transport or alter video parameters.
- **Test scenarios:**
  - State includes ingress/decode/publication/resolution/decoder/error, never raw RTP.
  - Invalid IP does not modify the overlay.
  - Port busy leaves transport alive.
  - Requests cannot change ports, RTP or lifecycle.
- **Verification:** Dashboard is useful when opened, irrelevant when closed and has no GStreamer callback work.

### U5. Prove Primary using a props-off bench

- **Goal:** Decide whether freezes are source/link, H.264 recovery, Edge delivery or RViz before changing Android rate/bitrate/resolution.
- **Requirements:** R4–R6, AE3.
- **Dependencies:** U1–U4.
- **Files:** `docs/PRIMARY_CPP_BENCH.md`, `README.md`, `docs/PROTOCOL_V1.md`, `src/dji_edge_transport/test/test_protocol_decoder.cpp`, `src/dji_edge_transport/test/test_transport_node.cpp`.
- **Approach:** Define a 60-second real-stream run that records status/topic progression only. Compare Android pre-network health with Edge post-network ingress/decode/publication, keeping timing domains explicitly separate. Record resolution, source rate, bitrate, decoder, ages, supersession, clock and RViz evidence. Correct old documentation that implies an Edge restart after a stall.
- **Test scenarios:**
  - Canonical telemetry, AU metadata and clock packets preserve source timestamps.
  - Documentation distinguishes Android pre-network from Edge post-network evidence.
  - No-stream is “waiting for UDP,” not a performance result.
  - Documentation does not promise lost-UDP recovery or automatic recreation.
- **Verification:** The bench names the first non-progressing boundary and recommends retaining 30 FPS, tuning Android/link, or investigating ROS/RViz separately.

---

## Verification Contract

| Gate | Applies to | Evidence |
| --- | --- | --- |
| Unit and protocol tests | U1–U4 | Counter semantics, one-frame handoff, wake coalescing, decoder selection, protocol compatibility and dashboard isolation pass in Humble. |
| Static hot-path audit | U1–U4 | One appsink/one handoff; no preview, relay, watchdog, raw capture, payload metrics or ROS publish from GStreamer callback. |
| Container build/test | U1–U4 | Project Humble service builds/tests in Release with GPU-capable and CPU-only preflight paths. |
| Headless graph smoke | U2–U4 | Starts without X11, exposes image/navigation/context/status/diagnostics and survives dashboard bind failure. |
| Props-off Primary bench | U5 | Real tablet stream advances ingress/decode/publication and captures resolution, bitrate, decoder, ages, rates, clock and RViz observation. |
| Compatibility regression | U1–U5 | Android ports/schema/source-time, mapper topics and dashboard clock configuration remain compatible. |

---

## Definition of Done

- Primary has one headless GStreamer pipeline, runtime-verified NVIDIA decode when available, CPU fallback otherwise, one appsink and one latest-frame handoff.
- There is no automatic decoder restart, preview branch, relay, default raw RTP capture or payload-level RTP metric path.
- ROS delivery is coalesced, event-driven and bounded to the newest image without DDS work in the streaming callback.
- Status/dashboard distinguish ingress, decode, ROS publication and renderer-only symptoms through compact metrics.
- Dashboard remains loopback-only, optional, non-fatal and limited to clock-target persistence.
- Humble build/test and headless CPU smoke pass; an NVIDIA runtime truthfully reports the selected decoder.
- A 60-second props-off Primary bench yields evidence for retaining or changing Android source settings.
- FPV, mapper internals, Android code and original Android/DJI timestamps remain unchanged.

---

## Appendix

### Current Baseline

`dji_edge_transport` already has a C++ Primary GStreamer pipeline ending at `appsink`, BGR conversion, a one-item `FrameHandoff`, sensor QoS publication and unavailable frame context rather than guessed AU identity. It has no preview branch or restart watchdog. Its remaining gaps are the 16 ms timer and incomplete progress evidence.

### Risks and Mitigation

- **ROS wake-up blocks decode:** publication stays outside the GStreamer callback and coalescing is characterized first.
- **GPU runtime differs from host:** select NVIDIA only after actual factory/PLAYING success; retain CPU fallback.
- **UDP loss looks like an Edge fault:** compare Android pre-network and Edge post-network counters; wait for IDR instead of restarting.
- **Apparent smoothness is old imagery:** reject capacity above one and verify age/supersession.
- **Dashboard leaks into transport:** retain loopback snapshot semantics and test port failure as non-fatal.

### Deferred to Follow-Up Work

- Independently isolated FPV transport after Primary proof.
- Bench-only raw RTP capture.
- GPU-native ROS image transport only if the new bench proves the CPU image boundary is material.
- Android bitrate, resolution, 20 FPS and IDR-cadence changes based on U5 evidence.
