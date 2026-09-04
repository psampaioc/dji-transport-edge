---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
planning_depth: deep
updated: 2026-09-03
---

# Low-Latency Direct ROS 2 Edge Workspace

## Goal Capsule

Deliver one Dockerized ROS 2 Humble edge application with exactly two packages under `src/`.

`dji_edge_driver` receives the frozen Android UDP/JSON/RTP contract directly, decodes each H.264 feed once, publishes current video and one compact navigation message, and records lightweight diagnostic evidence.

`dji_edge_mapper` consumes that navigation message and maintains the static point-cloud map, drone pose, TF, and continuous RTK-preferred/GPS-fallback path.

The immediate priority is restoring the low-latency Primary stream without sacrificing reliability, while retaining the automatic GStreamer preview windows for Primary and FPV.

## Product Contract

### Requirements

- R1. Run the entire edge/ROS runtime inside the project Humble Docker service. The normal `djiedge` entry point opens an interactive project container; an optional convenience command may build and launch the full stack, but must not replace the explicit workflow.
- R2. Preserve the Android wire contract and ports: telemetry UDP `5500`/`5501`/`5502`, Primary RTP `5600`, FPV RTP `5610`, RTP payload type `96`. Do not change RTP or ports to diagnose FPV.
- R3. Publish current Primary and FPV video independently as ROS image topics. Native GStreamer preview windows for both feeds remain enabled by default and must stay usable even if a ROS consumer is slow.
- R4. Publish one compact typed ROS navigation topic, not independent raw flight/RTK/gimbal ROS topics. It must contain position, relative altitude, heading, position source/validity, gimbal pitch/validity, and sufficient Android/Edge timestamps to measure transport age.
- R5. Use RTK position when valid and in use; otherwise continue from GPS. Use aircraft-relative altitude for local map Z. Preserve heading for orientation and gimbal pitch for future detection association.
- R6. Keep an operational dashboard for configuration, transport state, latency, FPS, losses, RTK/GPS state, and evidence location. It must not render video or sit on the critical image path.
- R7. NDJSON evidence is always on and session-scoped. Raw RTP/H.264 capture is bench-only and opt-in. rosbag2 is an independent ROS-level recording tool, not a replacement for either one.
- R8. The shutdown sequence must stop GStreamer callbacks before ROS publishers/context are destroyed. A normal exit must not emit `RCLPublisher's context is invalid`.
- R9. Preserve the existing static `.pcd` map, RViz configuration, map topics, and manual-flight boundary. No code in this repository may command flight, mission, or gimbal movement.

### Scope Boundaries

- Android continues to own DJI callbacks, H.264 access-unit construction, RTP emission, and compact pre-network health. Rich secondary parser diagnostics stay in Android-local NDJSON during the FPV investigation.
- The current FPV fault is classified as Android-emission-side: Edge receives zero RTP packets on `5610`. The driver must remain ready for the feed, but cannot fabricate an FPV picture.
- Do not introduce `shmsink`, a third ROS package, a standalone Python receiver, an HTTP relay, loopback RTP relay, or a new video transport before measurements prove a need.
- A future C++/zero-copy bridge is explicitly deferred. Python `sensor_msgs/Image` necessarily copies decoded pixels for an active ROS subscriber; this plan makes that cost bounded and isolated first.

## Planning Contract

### Current Evidence and Problem Frame

Primary transport is healthy at the network boundary: recent bench evidence showed `1280x720`, about `30 FPS`, and about `3.3 Mbps` H.264/RTP arriving from the tablet.

The apparent ~`86 MB/s` is not Wi-Fi traffic. A 1280×720 BGR decoded frame is 2,764,800 bytes; at 30 FPS it is about 83 MB/s of local raw pixel movement. The current driver decodes each feed twice and makes a Python byte copy before publishing from the GStreamer streaming callback. That couples GStreamer to ROS/DDS backpressure and is the likely source of the severe accumulated display delay.

Telemetry evidence showed `flight`, `gimbal`, and temporary `battery` packets, but no valid RTK during the bench; the latter is a hardware/data-availability condition, not a mapper fault. Some Android telemetry datagrams were malformed JSON and correctly rejected. The Android health clock exchange was not configured, so no trustworthy one-way transport-latency figure exists yet.

### High-Level Technical Design

```mermaid
flowchart LR
  A[Android tablet] -->|JSON 5500/5501/5502| D[dji_edge_driver]
  A -->|Primary RTP 5600| GP[Primary GStreamer pipeline]
  A -->|FPV RTP 5610| GF[FPV GStreamer pipeline]
  GP --> P[Native preview]
  GF --> F[Native preview]
  GP --> I1[Latest-frame handoff]
  GF --> I2[Latest-frame handoff]
  I1 --> R1[/dji/edge/video/primary]
  I2 --> R2[/dji/edge/video/fpv]
  D --> N[/dji/edge/navigation]
  N --> M[dji_edge_mapper]
  M --> RV[Map pose path TF RViz]
  D --> E[Session NDJSON and dashboard]
```

The diagram is directional, not implementation code. Each feed has one decode, then two isolated consumers: an always-current native preview and an optional latest-frame ROS handoff. Diagnostics and evidence observe the pipeline without retaining frames.

### Key Technical Decisions

- KTD1. **One decode per feed.** Depayload, parse, decode, and color-convert once, then split to preview and appsink with leaky queues. This removes the duplicate decoder while keeping independent preview and ROS consumers. Governs R3, R6.
- KTD2. **Latest frame, never queued frames.** The appsink callback only maps a frame when there is a ROS subscriber and replaces a one-slot handoff buffer. A ROS worker/timer publishes that newest frame outside the GStreamer thread. Old frames are discarded rather than allowed to create latency. Governs R3, R8.
- KTD3. **Compact fused navigation.** Android may send compact `flight`, `rtk`, and `gimbal` packets, but the driver emits one `NavigationState` ROS message. It joins the latest valid values by monotonic time and exposes source/validity rather than hiding fallback. Governs R4, R5.
- KTD4. **Session evidence, not permanent mixed logs.** Each driver process creates an `evidence/edge-<UTC>-<id>/` directory. It contains NDJSON and, only when enabled, RTP capture for the same run. This keeps historical evidence but makes a bench attributable. Governs R6, R7.
- KTD5. **Docker shell first.** `djiedge` opens the project container with the workspace mounted at `/workspace`; the operator explicitly runs build/source/launch there. `djiedge-run` may provide the former all-in-one behavior for convenience. Governs R1.
- KTD6. **Latency is measured at boundaries.** Dashboard/evidence label Android counters as pre-network and Edge receive/RTP/AU/decode/image metrics as post-network. The clock handshake is configured before reporting one-way transport delay. Governs R4, R6, R7.

### Android Input Contract to Retain

The Android agent should emit only compact valid JSON through its serializer, with session, sequence, and Android monotonic timestamp on every message.

| Packet type | Required `data.fields` | Purpose |
| --- | --- | --- |
| `flight` | `aircraft.latitude_deg`, `aircraft.longitude_deg`, `aircraft.altitude_m`, `heading_deg` | GPS fallback, local Z, yaw |
| `rtk` | `fusion.latitude_deg`, `fusion.longitude_deg`, `is_being_used` | Preferred horizontal position |
| `gimbal` | `attitude.pitch_deg` | Detection/camera orientation association |
| `health` | compact callbacks, AU/RTP production, queue/socket errors and rates | Pre-network diagnostic comparison only |
| `video_au` | feed, frame sequence, RTP timestamp/SSRC, first-byte and complete Android monotonic timestamps | Per-feed latency and integrity comparison |
| `clock` | ping/pong timestamps | Clock-offset and latency measurement |

No raw video bytes, parser dumps, battery, or nonessential DJI telemetry belong in this edge transport contract.

## Implementation Units

### U5. Correct the Docker operating model and documentation

- **Goal:** Make the explicit container-first workflow the normal user experience without breaking the existing convenience launcher.
- **Files:** `README.md`, `src/dji_edge_driver/scripts/dji_edge_bringup.sh`, `src/dji_edge_driver/launch/dji_edge_driver.launch.py`, `docs/` operational guide; external Docker Compose and shell aliases are updated as environment configuration, not copied into this repository.
- **Patterns:** Keep the current project-scoped Humble service, host networking, X11 support, `/workspace` bind mount, and user identity. Make building explicit or conditional; launching must not silently rebuild on every run.
- **Test scenarios:** From an arbitrary host directory, `djiedge` enters a shell rooted at `/workspace`; the explicit build/source/launch sequence starts both packages; `djiedge-run` performs the same sequence only when requested; exit leaves no driver process or occupied dashboard/UDP ports.
- **Verification:** Confirm only the project Compose service is used and that runtime artifacts remain root `build/`, `install/`, `log/`.

### U6. Make compact navigation the only ROS telemetry contract

- **Goal:** Extend and trim `NavigationState` so it is sufficient for map/path, camera orientation association, and transport measurement while removing unnecessary raw telemetry ROS publishers.
- **Files:** `src/dji_edge_driver/msg/NavigationState.msg`, `src/dji_edge_driver/scripts/dji_edge_driver_node.py`, `src/dji_edge_driver/dji_edge_transport_core/navigation.py`, `src/dji_edge_driver/config/bridge.yaml`, `src/dji_edge_mapper/scripts/drone_localization_node.py`, `src/dji_edge_driver/test/test_transport_core.py`.
- **Patterns:** Retain tolerant ingress parsing during Android migration, but expose only fused navigation to ROS. Merge latest valid gimbal pitch with its source timestamp. Keep health and raw ingress only in driver state/dashboard/evidence.
- **Test scenarios:** RTK valid/in-use wins; GPS produces a continuous fallback; missing/invalid RTK does not invalidate a valid GPS solution; gimbal pitch is carried with validity and timestamp; malformed or extra Android fields do not crash the driver; mapper preserves path/map semantics using the revised message.
- **Verification:** `ros2 interface show dji_edge_driver/msg/NavigationState` and package tests demonstrate the compact public topic has every field required by R4/R5 and no raw flight/RTK/gimbal ROS topic remains.

### U7. Decouple video decode, preview, ROS publication, and shutdown

- **Goal:** Remove accumulated video latency and the ROS-context shutdown race while retaining automatic Primary/FPV native windows and current-image ROS topics.
- **Files:** `src/dji_edge_driver/scripts/dji_edge_driver_node.py`, `src/dji_edge_driver/dji_edge_transport_core/rtp.py`, `src/dji_edge_driver/test/test_transport_core.py`, `src/dji_edge_driver/launch/dji_edge_driver.launch.py`.
- **Patterns:** Build one post-decode tee per feed. Preview and ROS branches each use bounded leaky queues. Avoid BGR-byte allocation when no image subscriber exists. Use a one-slot latest-frame handoff and a ROS-owned publisher worker; never invoke `publish()` from GStreamer. Stop accepting samples, detach/guard callbacks, set pipelines NULL, join workers, then destroy ROS publishers/context.
- **Test scenarios:** A Primary RTP fixture produces an image and preview branch; a slow/no ROS subscriber cannot grow a queue or slow the latest preview; subscriber connect/disconnect changes copy behavior safely; FPV pipeline starts cleanly with no packets; shutdown while samples arrive produces no `RCLError` and no orphan pipeline; raw capture remains off unless enabled.
- **Verification:** A 60-second Primary bench compares no-image-subscriber and active-image-subscriber runs. It records input AU FPS, decoded FPS, ROS publish FPS, frame age, drops, CPU, and resident memory. Preview remains current; ROS may drop old frames but must not accumulate delay.

### U8. Make evidence and latency measurements session-correct

- **Goal:** Make every hardware run attributable and turn latency from an inference into a measured value.
- **Files:** `src/dji_edge_driver/dji_edge_transport_core/evidence.py`, `src/dji_edge_driver/dji_edge_transport_core/dashboard.py`, `src/dji_edge_driver/scripts/dji_edge_driver_node.py`, `src/dji_edge_driver/config/bridge.yaml`, `src/dji_edge_driver/test/test_transport_core.py`, `README.md`.
- **Patterns:** Generate a unique session directory at startup and report it in state/dashboard. Keep NDJSON low-rate and append-only inside that session. Store raw RTP only under the same session when `capture_rtp=true`. Configure the tablet clock peer explicitly and report `clock.ready=false` rather than a synthetic latency.
- **Test scenarios:** Consecutive runs produce separate directories; write failure is surfaced but does not stop live transport; raw RTP is absent by default and present only when requested; a clock-unready run reports unavailable latency; a valid ping/pong produces offset/age fields; dashboard labels pre-network versus post-network metrics.
- **Verification:** Inspect a newly created evidence session after a bench and correlate Android `health`/`video_au` against Edge RTP/AU/image observations without historical log contamination.

### U9. Run the hardware acceptance bench and close boundaries

- **Goal:** Prove the revised Primary path and document the precise FPV/telemetry dependencies rather than guessing from windows.
- **Files:** `README.md`, `docs/` bench guide, `src/dji_edge_driver/test/` only if reusable characterization fixtures emerge.
- **Patterns:** Bench props-off. Start with Primary while Android remains unchanged; then repeat after Android emits the compact contract and clock peer. Treat FPV as a separate feed with its own boundary evidence.
- **Test scenarios:** Primary arrives on `5600`, has valid RTP/AUs, decodes, previews, and publishes images; FPV is either identical through its entire chain or recorded as `Android emitted none` before any edge failure; navigation becomes non-null with valid flight coordinates; RTK preference and GPS fallback are observable; gimbal pitch is non-null when Android supplies it; clean shutdown is error-free.
- **Verification:** Save a session report containing stream resolution, input bitrate/FPS, decoded/ROS FPS, RTP gaps, dropped-old-frame count, clock state, navigation source, and exact FPV failed boundary. Establish the current latency baseline before setting an acceptance target; use a physical screen comparison to validate end-to-end display delay.

## Verification Contract

| Gate | Applies to | Proof |
| --- | --- | --- |
| Workspace build | U5-U9 | Dockerized Humble `colcon build --symlink-install` discovers only `dji_edge_driver` and `dji_edge_mapper`. |
| Unit and interface tests | U6-U8 | `colcon test` passes transport, navigation, evidence, lifecycle, and mapper cases; `colcon test-result --verbose` has no failures. |
| Static architecture audit | U5-U8 | No runtime dependency on the removed standalone receiver, HTTP relay, loopback RTP ports, or nested `ros2_ws`; exactly two package manifests exist under `src/`. |
| Launch smoke | U5-U8 | Explicit container workflow starts driver and mapper, map/RViz assets remain available, dashboard is reachable, and Ctrl-C/Exit leaves no process or port leak. |
| Video bench | U7-U9 | 60-second Primary measurements exist with and without an image subscriber. The preview stays current and the ROS branch has bounded latest-frame behavior. |
| Hardware contract bench | U6-U9 | Evidence identifies pre-network Android state versus post-network Edge state; clock is either demonstrably ready or accurately unavailable; FPV first failing boundary is recorded. |

## Definition of Done

- The root workspace has only `src/dji_edge_driver` and `src/dji_edge_mapper`; root `build/`, `install/`, and `log/` are generated and ignored.
- The normal documented workflow is: enter the dedicated Humble container, build/source once when code changes, then launch the two-package stack. The convenience launcher remains optional.
- The driver directly owns the five frozen Android ports and publishes separate Primary/FPV current-image topics plus one compact `NavigationState` topic.
- GStreamer decodes each feed once. Slow ROS consumers cannot delay preview or accumulate old frames. No image bytes are copied when there is no image subscriber.
- Navigation includes RTK-preferred/GPS-fallback horizontal location, relative altitude, heading, and gimbal pitch with validity/timing information; the mapper continues to publish the map, pose, path, and TF.
- NDJSON evidence is session-scoped and always on; raw RTP capture is off by default and opt-in; rosbag2 remains a separate explicit capture.
- Dashboard is monitoring/configuration only and reports unambiguous pre-network/post-network metrics, evidence path, stream resolution/FPS/losses, and clock readiness.
- Normal shutdown has no `RCLPublisher's context is invalid`, orphan GStreamer processes, or retained ports.
- Props-off Primary acceptance evidence exists. FPV is either working through ROS/preview or has an evidence-backed first failure at Android emission, Edge receive, RTP validity, AU construction, decode, or ROS publication.
