---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: 2026-09-02-1945-refactor-direct-ros-edge-plan.md
planning_depth: deep
updated: 2026-09-02
---

# Direct ROS 2 Humble Edge Transport — Continuation Plan

## Goal Capsule

Finish the direct ROS 2 Humble edge runtime so it replaces the operational chain `dji-edge receiver -> HTTP state / local RTP relay -> ROS bridge` without changing the Android wire contract. The resulting one-command Docker launch receives Android UDP/JSON/RTP directly, publishes navigation, telemetry and two image feeds, preserves mapper/RViz behavior, records trustworthy evidence, opens Primary and FPV GStreamer previews, and serves a local measurement dashboard.

The direct code committed so far is a working vertical slice, not parity-complete. This continuation converts it into the production-shaped implementation. The native receiver remains unchanged as a rollback and characterization source until direct-path hardware parity is evidenced; it is not part of the new operational launch path.

## Verified Continuation Baseline

The feature branch contains direct-bind scaffolding and no map/mapper rewrite:

- `dji_edge_bridge` currently binds `5500`, `5501`, `5502`, `5600`, and `5610`, emits an initial `NavigationState`, writes simple NDJSON, and launches one GStreamer pipeline per feed.
- The bridge currently imports protocol/state/clock classes from the repository-root `dji_edge_receiver` package through an external Compose mount. That is temporary and must disappear from the installed direct runtime.
- Humble builds for `dji_edge_bridge` and `dji_edge_mapper` passed. `h264parse`, `rtph264depay`, and `ximagesink` are present in the Humble image. A synthetic direct navigation message was observed.
- The current bridge still lacks full endpoint-specific validation/reassembly, active clock probing, packet/AU metrics, raw RTP opt-in capture, dashboard ownership, comprehensive tests, and a reliable launch lifecycle.
- `dji_edge_mapper`, its map assets, mapper configuration, and RViz configuration are preserved. No cleanup/delete step is authorized before the hardware parity gate.

## Product Contract

### Requirements

- R1: Receive Android datagrams directly on UDP `5500` (telemetry and compact health), `5501` (frame metadata), `5502` (clock), `5600` (Primary RTP), and `5610` (secondary/FPV RTP). Do not change ports, RTP payload type `96`, or packetization.
- R2: Publish Primary and FPV images directly from their own RTP feeds; publish validated flight, RTK, gimbal, frame-metadata, navigation, diagnostics, and transport metrics directly to ROS 2.
- R3: Keep navigation continuous: select RTK for X/Y only when valid and in use; otherwise use GPS; use `aircraft.altitude_m` for local Z; retain RTK vertical altitude as diagnostic until datum calibration.
- R4: The dashboard is browser-only state and measurement UI. It shows editable safe configuration, copyable Ubuntu address, pre-network Android values, post-network Edge values, loss, FPS, resolution, bitrate, clock estimate, RTK/GPS, evidence, and capture controls. It deliberately does not render video.
- R5: NDJSON evidence is always on. Raw RTP/H.264 is bench-only opt-in. rosbag2 remains a separate ROS-level replay/recording workflow.
- R6: Android `health` remains compact and stable: session, sequence, Android monotonic time, queue/socket errors and drops, callback counts/rates, and emitted AU/RTP counts. Android parser dumps remain Android-local diagnostic NDJSON.
- R7: One direct Humble Docker launch opens the dashboard plus both native GStreamer preview windows by default, and can stop cleanly through Ctrl-C or dashboard Exit.

### Scope boundaries

In scope: Ubuntu-side direct ingress, GStreamer depayload/decode, evidence, dashboard migration, configuration/launch/Docker ergonomics, direct ROS topics, measurement, tests, and documentation.

Out of scope: Android transport redesign, port/RTP contract changes, flight commands, camera source routing, person detection/segmentation, RTK datum calibration, autonomous operations, and deleting the legacy receiver.

### Deferred work

- A bounded, typed, low-rate ROS diagnostic for Android FPV parser internals if Android-local NDJSON plus direct Edge metrics cannot isolate FPV.
- rosbag2 profiles and replay tooling after direct hardware parity.
- Hardware decoder/GPU selection after the CPU baseline is measured.

## Key Technical Decisions

### KTD1 — One direct runtime owns ingress

`dji_edge_bridge` owns all five UDP inputs, direct validated state, clock mapping, GStreamer pipelines, ROS publication, evidence, dashboard server, and shutdown. `dji_edge_mapper` remains separate because map/path ownership is distinct. The direct runtime must be self-contained in the installed ROS workspace; it may reuse semantics from `dji_edge_receiver`, but it must not import it through a source mount at runtime.

### KTD2 — GStreamer owns H.264 depayload and decode

Python validates and measures RTP at GStreamer’s input boundary but does not depayload or decode H.264. Each feed has one direct `udpsrc` pipeline. A leaky appsink branch publishes ROS images and an independent native display branch opens a diagnostic window. Preview is enabled initially and may consume a second decode; it is configurable for later measurement.

### KTD3 — Evidence layers prove different boundaries

NDJSON continuously records validated JSON, clock samples, state transitions, counters, and errors. Optional `.rtpbin` captures full compressed RTP datagrams before depayload for bounded bench/FPV diagnosis. rosbag2 records ROS messages after ingress/decode for downstream replay. These are not substitutes for each other.

### KTD4 — Metrics retain their boundary labels

Android health is **pre-network**: callbacks, parser/AU/RTP emission, and Android drops/errors. Edge metrics are **post-network**: received datagrams/bytes, RTP sequence dispositions, observed AUs, decoded frames, pipeline state, FPS, bitrate, and evidence health. The dashboard may calculate deltas only when session and feed identity match; it must otherwise label them incomparable. Clock mapping estimates transport timing but never claims camera-capture time.

## High-Level Design

```text
Galaxy Tab S9
  health/telemetry 5500 ─┐
  frame metadata    5501 ├──> Direct ROS edge runtime
  clock probes       5502 │      ├─ validate/reassemble/latest state -> ROS topics
  Primary RTP        5600 ├──────┼─ GStreamer Primary -> Image + native preview
  FPV RTP            5610 ┘      ├─ GStreamer FPV ----> Image + native preview
                                  ├─ NDJSON / optional raw RTP capture
                                  └─ localhost dashboard -> browser

Direct ROS topics -> mapper -> map, TF, pose, path -> RViz
```

```text
Android health (pre-network)        ─┐
                                    ├─ compare only by session and feed
Edge ingress/video (post-network)   ─┘

raw RTP: datagram at Edge before depayload
NDJSON: validated events and bounded measurements
rosbag2: messages after ROS ingress/decode
```

## Intended ROS Contract

| Topic | Type | Meaning / QoS |
|---|---|---|
| `/dji/navigation/state` | `dji_edge_bridge/NavigationState` | Normalized RTK-or-GPS state; reliable, keep last. |
| `/dji/telemetry/flight`, `/dji/telemetry/rtk`, `/dji/telemetry/gimbal` | `std_msgs/String` | Validated source records; not presented as synchronized. |
| `/dji/telemetry/frame_metadata` | `std_msgs/String` | Validated Android frame/AU metadata. |
| `/dji/primary/image_raw`, `/dji/fpv/image_raw` | `sensor_msgs/Image` | Direct decoded BGR frames; best effort, depth 1. |
| `/dji/diagnostics` | `diagnostic_msgs/DiagnosticArray` | Runtime health and boundary-labelled state. |
| `/dji/edge/transport_metrics` | `diagnostic_msgs/DiagnosticArray` | Per-feed Edge packet/AU/decode metrics. |

`/map/cloud`, `/dji/navigation/pose`, `/dji/navigation/path`, TF, `NavigationState` source codes, map assets, and RViz behavior remain unchanged.

## Target Structure

```text
ros2_ws/
  config/edge_transport.toml              # operator-owned runtime preferences
  src/dji_edge_bridge/
    dji_edge_bridge/                      # node entrypoint only
    dji_edge_transport_core/              # installed protocol/state/clock/evidence modules
    dashboard_static/                     # dashboard assets
    launch/ config/ test/
  src/dji_edge_mapper/                    # unchanged map/path package
  docs/                                   # operation and evidence guide
```

The CMake package continues to generate `NavigationState`. The private Python core uses a distinct module name, so it cannot collide with generated `dji_edge_bridge` interfaces.

## Implementation Units

### U1 — Replace the temporary direct prototype with an installed transport core

**Requirements:** R1, R3, R6. **Decisions:** KTD1, KTD4.

**Files:** `ros2_ws/src/dji_edge_bridge/CMakeLists.txt`, `ros2_ws/src/dji_edge_bridge/package.xml`, `ros2_ws/src/dji_edge_bridge/dji_edge_bridge/bridge_node.py`, `ros2_ws/src/dji_edge_bridge/dji_edge_transport_core/`, `ros2_ws/src/dji_edge_bridge/test/test_protocol_state.py`, `ros2_ws/src/dji_edge_bridge/test/test_clock.py`.

**Approach:** First convert legacy accepted/rejected behavior into characterization tests using `deploy/protocol/v1/golden_packets.json` and the receiver’s existing tests as the behavioral reference. Move or adapt only ROS-independent protocol decoding, sequence tracking, telemetry-fragment completion, latest snapshot association, RTP header accounting primitives, clock mapping, and bounded evidence semantics into an installed private module. Keep `bridge_node.py` an orchestration layer rather than a compressed all-in-one implementation. Remove the external repository-root Python mount/import from the runtime only after these tests pass.

**Test scenarios:**

- Valid telemetry, metadata, clock, and RTP fixtures retain their parsed normalized values.
- Malformed, over-limit, duplicate, incomplete, stale, and out-of-order fragments are rejected or held and never replace a completed latest state.
- Frame association retains valid prior telemetry where that is the established state contract.
- Installed workspace imports no `dji_edge_receiver` module and the direct launch contains no `5602`, `5612`, state URL, or HTTP polling dependency.

### U2 — Implement endpoint-specific ingress, clock service, evidence, and state topics

**Requirements:** R1, R2, R3, R5, R6. **Decisions:** KTD1, KTD3, KTD4.

**Files:** `ros2_ws/src/dji_edge_bridge/dji_edge_bridge/bridge_node.py`, `ros2_ws/src/dji_edge_bridge/dji_edge_transport_core/ingress.py`, `ros2_ws/src/dji_edge_bridge/dji_edge_transport_core/evidence.py`, `ros2_ws/src/dji_edge_bridge/config/bridge.yaml`, `ros2_ws/src/dji_edge_bridge/test/test_ingress_node.py`, `ros2_ws/src/dji_edge_bridge/test/test_navigation_contract.py`.

**Approach:** Give `5500`, `5501`, and `5502` distinct endpoint contracts. Ingress threads do bounded receive, size check, decode, endpoint/type validation, receive timestamping, state update, and non-blocking evidence enqueue; publication occurs only for completed relevant state. Add an active clock pinger aimed at the configured tablet IP, consume only `clock_pong` on `5502`, retain estimate quality/failure state, and set navigation transport age only when a valid mapping exists. Publish raw source records, navigation, ordinary diagnostics, and a separate transport-metrics diagnostic without a polling timer.

**Test scenarios:**

- A complete flight/RTK/gimbal sequence produces expected source records and one valid normalized navigation publication per new state.
- Valid/in-use RTK selects `POSITION_RTK`; invalid/not-in-use RTK selects `POSITION_GPS_FALLBACK`; missing usable coordinates produce no false position.
- Endpoint/type mismatch, bad clock payload, and socket failure increase the correct post-network/error counters without stopping other endpoints.
- A full evidence queue or writer error degrades diagnostics while ingress and ROS publication continue.
- Known synthetic clock offset is estimated; no tablet response remains non-fatal and makes latency unavailable rather than fabricated.

### U3 — Make direct RTP/GStreamer observability and capture parity-complete

**Requirements:** R1, R2, R5, R7. **Decisions:** KTD1, KTD2, KTD3, KTD4.

**Files:** `ros2_ws/src/dji_edge_bridge/dji_edge_bridge/bridge_node.py`, `ros2_ws/src/dji_edge_bridge/dji_edge_transport_core/rtp.py`, `ros2_ws/src/dji_edge_bridge/dji_edge_transport_core/evidence.py`, `ros2_ws/src/dji_edge_bridge/config/bridge.yaml`, `ros2_ws/src/dji_edge_bridge/test/test_rtp_metrics.py`, `ros2_ws/src/dji_edge_bridge/test/test_video_pipeline.py`.

**Approach:** Bind one direct `udpsrc` pipeline per feed at `5600` and `5610`. Use an input-boundary probe, not a second Python socket, to validate RTP version/payload type and track packet/byte count, gap, duplicate, out-of-order, rejection, marker-delimited access units, RTP timestamp, estimated bitrate/FPS, decoded frame count/resolution, and per-pipeline error/state. Branch after parse to a leaky appsink and a native display branch. Start both preview branches by default; a feed with no traffic or negotiation failure reports its own state without taking down telemetry or the other feed.

When raw capture is enabled, asynchronously record the complete datagram plus Edge monotonic receive time in documented `.rtpbin` framing before depayload. With raw capture disabled, retain NDJSON events/metrics only. Correlate capture session using valid frame metadata when possible and use a clear unknown-session fallback.

**Test scenarios:**

- A local valid RTP fixture reaches the appsink/image publication path and updates decoded frame, resolution, AU, and packet metrics.
- Both pipeline/preview branches construct with no incoming traffic and remain independently diagnosable.
- Malformed, reordered, duplicate, gapped, and marker-delimited packets update distinct counters and are never reported as decoded images.
- Capture on/off creates `.rtpbin` only while enabled; NDJSON continues in both modes and recorded framing can be parsed by a test reader.

### U4 — Migrate the dashboard to the direct runtime

**Requirements:** R4, R5, R6, R7. **Decisions:** KTD1, KTD3, KTD4.

**Files:** `ros2_ws/src/dji_edge_bridge/dashboard_static/index.html`, `ros2_ws/src/dji_edge_bridge/dashboard_static/app.js`, `ros2_ws/src/dji_edge_bridge/dashboard_static/app.css`, `ros2_ws/src/dji_edge_bridge/dji_edge_transport_core/dashboard.py`, `ros2_ws/config/edge_transport.toml`, `ros2_ws/src/dji_edge_bridge/test/test_dashboard.py`, `ros2_ws/src/dji_edge_bridge/CMakeLists.txt`.

**Approach:** Reuse the established dashboard visual/design behavior but make the direct in-process metric/state store its sole source. Serve only on localhost. Keep operator-editable tablet IP, raw-capture default, bench duration, and dashboard preferences in `ros2_ws/config/edge_transport.toml`; validate before atomic save and retain the last valid configuration. Apply settings that require socket/pipeline recreation only through controlled restart. Display selectable/copyable Ubuntu IP, active tablet IP, port map, data freshness, Primary/FPV resolution/FPS/loss/bitrate, Android pre-network versus Edge post-network metrics, clock-estimate quality, RTK/GPS, evidence state, capture state, bench report location, restart, and clean exit. Dashboard opens the browser only; native previews/RViz own video.

**Test scenarios:**

- Dashboard/API fixture reports direct store values for both feeds, evidence, Android health, and Edge metrics without querying legacy HTTP.
- Valid IP/config persists across restart; invalid input does not damage the last known-good TOML.
- Disabling raw capture through the UI prevents binary growth during a stream while telemetry/metadata NDJSON continues.
- Exit closes HTTP/dashboard, ROS node, GStreamer pipelines, and the opened dashboard tab/webview, then releases ports.

### U5 — Deliver the operational launch, Docker ergonomics, documentation, and mapper regression gate

**Requirements:** R2, R3, R7. **Decisions:** KTD1, KTD2.

**Files:** `ros2_ws/src/dji_edge_bridge/launch/dji_edge_bridge.launch.py`, `ros2_ws/src/dji_edge_bridge/launch/dji_edge_bringup.launch.py`, `ros2_ws/src/dji_edge_bridge/config/bridge.yaml`, `ros2_ws/config/edge_transport.toml`, `ros2_ws/docs/GUIA_DE_UTILIZACAO.md`, `scripts/djiedge`, `README.md`.

**External host configuration to update and verify:** the existing sibling Humble Dockerfile/Compose service and the user’s shell aliases. The direct service keeps host networking, X11, and a writable `ros2_ws` mount, but drops the repository-root compatibility mount once U1 completes.

**Approach:** Make the direct launch the one source of lifecycle ownership and retain the combined bridge+mapper bringup. Package dashboard assets and private modules without an `ament_python` collision against the generated interface package. Provide one non-interactive `djiedge` operational command which enters the workspace context, starts the direct combined launch, opens dashboard and both previews, and stops the entire process tree cleanly. Keep separately named shell/attach helpers for development. Update the guide with the visual pipeline, all commands/flags, configuration meaning, dashboard/browser distinction, capture/evidence semantics, bench procedure, startup/shutdown, troubleshooting, and known FPV limitation. Preserve mapper/RViz config and map assets unchanged.

**Test scenarios:**

- Build and test the installed workspace in Humble; launch direct bridge and combined bringup without native `dji-edge` or `/edge` compatibility mount.
- A controlled navigation input reaches mapper pose/path while the preserved map/RViz configuration loads.
- With X11, the dashboard plus two GStreamer previews start; without X11, an actionable preview/display diagnostic appears while ingress still runs.
- Ctrl-C and dashboard Exit release all five ports and permit a second clean launch; test supervision must not leave timeout-killed wrapper/container or child-process orphans.

### U6 — Run parity evidence, FPV classification, and make the retirement decision

**Requirements:** R1-R7. **Decisions:** KTD1-KTD4.

**Files:** `ros2_ws/docs/GUIA_DE_UTILIZACAO.md`, `ros2_ws/docs/BENCHMARK_TRANSPORT.md`, `scripts/capture_transport_bench.py`, `ros2_ws/src/dji_edge_bridge/test/test_bench_report.py`.

**Approach:** Produce one bounded bench report that records configuration, session/feed labels, Android pre-network counters, Edge post-network counters, clock state, navigation freshness/source, source/decoded frames, resolution/FPS/bitrate, RTP gaps, evidence drops, and disk growth. Run Primary parity first and FPV only after Android-side work is available. FPV is working only if direct `5610` receives valid packets, observes AUs, constructs/negotiates a decodable pipeline, and publishes images/preview. Use direct evidence to identify the first failed boundary; do not infer FPV state from window visibility alone.

After hardware gates pass, document the old receiver as deprecated rollback and prepare a separate reviewable cleanup plan. Do not delete the legacy receiver or its tests in this plan.

**Test scenarios:**

- A 60-second Primary bench displays matching session/feed context and clearly separated Android emitted versus Edge received counters.
- FPV evidence classifies one of: Android emits nothing, Edge receives nothing, invalid RTP, no AUs, parse/decode failure, or no decoded images.
- A long raw-capture-off session retains NDJSON/evidence health and excludes full video payload growth.
- Direct path proves no HTTP polling/loopback relay in the operational measurement chain.

## Dependency Order

```text
U1 characterization + installed core
 └─> U2 control ingress / clock / state / evidence
      ├─> U3 direct RTP / GStreamer / capture metrics
      │    └─> U4 direct dashboard
      └─> U5 launch / Docker / mapper / docs
             └─> U6 hardware parity / FPV classification / retirement decision
```

## Verification Contract

### Automated gates

- Legacy receiver tests continue to pass before their behavior is extracted as characterization coverage.
- Direct bridge tests cover all JSON packet families, endpoint/type rejection, fragment state, clock quality, evidence failure behavior, RTP metrics/framing, dashboard config, and shutdown.
- Humble `colcon build --symlink-install` and `colcon test` pass for bridge and mapper from the installed workspace.
- Isolated launch smoke tests prove no operational dependency on `127.0.0.1:8088`, `5602`, `5612`, `dji-edge`, or the repository-root source mount.
- Lifecycle tests start and stop a supervised direct process and verify ports are released; they must not leave timeout-killed wrapper/container or child-process orphans.
- Mapper regression test confirms preserved map/pose/path behavior from controlled navigation input.

### Hardware bench gate

Props-off, tablet connected to Cendence/drone, Ubuntu reachable from tablet:

1. Run the direct operational command; verify dashboard, Primary preview, and FPV preview windows start.
2. Verify displayed Ubuntu/tablet IP and configure Android transport to that Ubuntu address.
3. Capture a 60-second raw-RTP-on diagnostic session; retain generated bench report and evidence directory.
4. Check telemetry/RTK/GPS freshness, compact Android health, Edge UDP/RTP metrics, gaps, AUs, FPS/bitrate, decoded image resolution/FPS, clock quality, and evidence health.
5. Repeat with raw RTP off for a sustained session; confirm NDJSON remains active and full compressed payloads do not grow on disk.
6. If FPV is absent, classify its first failing boundary from direct evidence and hand only that bounded evidence to the Android-side investigation.

## Definition of Done

- One self-contained ROS 2 Humble direct launch receives the unchanged Android protocol and has no native receiver, legacy HTTP, relay-port, or external source-mount runtime dependency.
- Primary and FPV each start an independently reported native preview pipeline and direct ROS image topic; errors, lack of traffic, and decode failures remain feed-specific.
- Navigation/map behavior stays intact: RTK preferred, GPS continuous fallback, relative aircraft altitude for local Z.
- Dashboard presents validated, boundary-labelled state, persists safe tablet/capture preferences, and can restart/exit cleanly.
- NDJSON remains always on, raw RTP is explicitly opt-in, and rosbag2 is documented as separate.
- Automated gates pass. Primary and FPV hardware bench results are evidence-backed, including an explicit FPV failure classification if Android packetization is still unresolved.
- Legacy receiver is retained untouched as rollback until a separately approved cleanup following parity evidence.

## Risks and Open Questions

- **FPV is still Android-side unknown.** Edge improvements can localize but cannot decode absent/invalid secondary datagrams. Android owns packet production; Edge owns boundary classification.
- **Native preview can double decode load.** It is intentionally on during validation. Measure it before deciding whether RViz-only preview is sufficient.
- **Clock estimates are not camera timestamps.** UI and docs must say this plainly and hide transport latency if mapping quality is unavailable.
- **Raw RTP can consume disk quickly.** Its active state, destination, and disk use must be visible; long runs default it off.
- **Python/rosidl packaging has collision risk.** The distinct private core and installed-workspace smoke test are mandatory guardrails.
- **Hardware validation needs the operator.** Automated checks prove protocol/lifecycle only; they cannot prove sustained tablet/drone video or FPV delivery.
