---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
product_contract_source: ce-plan-bootstrap
planning_depth: deep
---

# Direct ROS 2 Humble Edge Transport Plan

## Goal Capsule

Replace the current two-process Ubuntu transport path with one ROS 2 Humble edge runtime.  It will ingest the Android UDP/JSON/RTP protocol directly, publish the existing navigation, telemetry, image, diagnostics, map, pose, and path topics, keep append-only NDJSON evidence, and provide the local measurement dashboard.  It must reduce avoidable state-poll latency without changing the Android wire contract or the map/navigation semantics.

The existing native `dji-edge` receiver remains available only during migration and is retired only after measured direct-path parity.  The direct runtime continues to open one GStreamer preview window for Primary and one for FPV at startup; RViz remains an additional viewer.

## Product Contract

### Requirements

- R1: Receive Android datagrams directly on UDP `5500` (telemetry/compact health), `5501` (frame metadata), `5502` (clock), `5600` (primary RTP), and `5610` (secondary/FPV RTP).  Do not change ports, RTP payload type `96`, or packetization without hardware evidence.
- R2: Publish primary and FPV images directly from their respective RTP feeds, plus raw validated flight, RTK, gimbal, frame-metadata, navigation, and diagnostic ROS topics.
- R3: Preserve continuous navigation: RTK is preferred for X/Y only when valid and in use; GPS remains the fallback.  Local Z remains `aircraft.altitude_m`; RTK vertical altitude is diagnostic until its datum is calibrated.
- R4: The dashboard is a browser UI for state and measurement only.  It must show configuration, network address, pre-network Android counters, post-network edge counters, loss, FPS, resolution, bitrate, clock/transport estimate, RTK/GPS status, evidence health, and capture controls.  Video is deliberately not embedded there.
- R5: NDJSON evidence is always on.  Full raw RTP/H.264 capture is an explicit bench mode.  rosbag2 is a separate ROS-level recording workflow.
- R6: Android `health` remains small and stable: session/sequence/monotonic time, queue/socket drops/errors, callbacks, emitted RTP/AU counters, and compact rates.  Android parser dumps never share that heartbeat.
- R7: The default operational path is one ROS launch command in the Humble Docker environment, opening the dashboard and the two diagnostic preview windows.  It must be possible to stop it cleanly.

### Scope boundaries

In scope: direct Ubuntu-side ingress, GStreamer depayload/decode, evidence, dashboard migration, configuration/launch/Docker ergonomics, ROS topics, measurement, tests, and docs.

Out of scope: Android transport redesign, changing the five ports, repairing Android FPV packetization, flight commands, camera source routing, person detection/segmentation, RTK datum calibration, and autonomous operations.

### Deferred to follow-up work

- A bounded typed, low-rate ROS diagnostic for Android FPV parser internals, if Android-local NDJSON and direct edge counters do not isolate the issue.
- rosbag2 recording profiles and replay tooling after direct topics have hardware parity.
- Hardware decoder selection and GPU optimization after baseline CPU decode is measured.

## Key Technical Decisions

### KTD1 — Direct ingress is owned by `dji_edge_bridge`

*(session-settled: user-directed — chosen over native receiver plus HTTP/RTP relay: remove polling and loopback relay latency/failure points.)*

`dji_edge_bridge` becomes the sole Ubuntu runtime owner of UDP sockets, clock mapping, validated state, GStreamer pipelines, ROS publication, evidence, and dashboard process lifetime.  The mapper remains a separate package because it has a distinct map/path responsibility.

### KTD2 — GStreamer owns H.264 depayload/decode

*(session-settled: user-directed — chosen over Python H.264 depayload/decode: lowest-friction native media path.)*

The runtime observes and validates RTP at the GStreamer input boundary, but does not depayload or decode H.264 in Python.  Each feed uses one direct `udpsrc` pipeline and branches after parsing: a bounded appsink branch publishes ROS images; a temporary native preview branch renders a visible GStreamer window.  The preview is diagnostic and may cost a second decode; it is configurable and removable after FPV validation.

### KTD3 — Evidence has three non-interchangeable layers

*(session-settled: user-directed — chosen over treating rosbag2 as raw transport evidence: each layer proves a different boundary.)*

NDJSON records validated UDP/JSON, clock samples, metadata, counters, and failures continuously.  Optional `.rtpbin` records capture complete compressed RTP datagrams before decode for bench/FPV diagnosis.  rosbag2 later records published ROS messages for replay and downstream development; it cannot prove loss that happened before ROS ingress or preserve the original RTP payloads.

### KTD4 — Dashboard compares boundaries explicitly

*(session-settled: user-directed — chosen over one blended health number: loss must be located, not inferred.)*

The dashboard labels Android values as **pre-network** and Ubuntu values as **post-network**.  It presents deltas only when their sessions, feed names, and monotonic timing make comparison valid; otherwise it reports the inputs as incomparable.

## High-Level Technical Design

This describes ownership and boundaries, not final class names.

```text
Galaxy Tab S9
  health/telemetry 5500 ─┐
  frame metadata    5501 ├──> Direct ROS edge runtime (one process)
  clock probes       5502 │      ├─ validate/reassemble/latest state ─> ROS topics
  primary RTP        5600 ├──────┼─ GStreamer primary ─> Image + preview window
  FPV RTP            5610 ┘      ├─ GStreamer FPV ─────> Image + preview window
                                  ├─ asynchronous NDJSON / optional RTP capture
                                  └─ localhost dashboard ─> browser
                                                           
ROS navigation/image topics ─> mapper ─> TF, map, pose, path ─> RViz
```

```text
Android health: callbacks/AUs/RTP emitted ── pre-network ─┐
                                                         compare only by session/feed
Edge: UDP bytes/RTP gaps/AUs/FPS/decoded frames ── post-network ─┘

RTP raw capture:  packet reaches edge before depayload
NDJSON:           validated control/metadata/events and bounded metrics
rosbag2:          ROS messages after ingress/decode
```

## Intended Topic Contract

Existing topic names stay stable unless a unit explicitly adds one.

| Topic | Type | Meaning / QoS |
|---|---|---|
| `/dji/navigation/state` | `dji_edge_bridge/NavigationState` | Normalized RTK-or-GPS navigation; reliable, keep last. |
| `/dji/telemetry/flight`, `/dji/telemetry/rtk`, `/dji/telemetry/gimbal` | `std_msgs/String` | Canonical validated source records, never asserted synchronized. |
| `/dji/telemetry/frame_metadata` | `std_msgs/String` | Validated Android frame/AU metadata. |
| `/dji/primary/image_raw`, `/dji/fpv/image_raw` | `sensor_msgs/Image` | Direct decoded frames; best effort, depth 1. |
| `/dji/diagnostics` | `diagnostic_msgs/DiagnosticArray` | Runtime health, including explicit Android/edge boundary labels. |
| `/dji/edge/transport_metrics` | `diagnostic_msgs/DiagnosticArray` | Per-feed post-network counters and derived estimates. |

The mapper topics (`/map/cloud`, `/dji/navigation/pose`, `/dji/navigation/path`, TF) and `NavigationState` source/fallback semantics remain unchanged.

## Output Structure

```text
ros2_ws/
  src/dji_edge_bridge/
    scripts/                 # direct ingress, protocol, evidence, dashboard runtime
    launch/                  # direct bridge and bridge+mapper bringup
    config/                  # packaged defaults
    dashboard_static/         # dashboard assets served by the ROS runtime
    test/                     # protocol, state, evidence, pipeline and dashboard tests
  config/edge_transport.toml # operator-owned editable runtime config
  docs/                      # command, evidence, dashboard and benchmark guides
../matrice-dji-sdk_v4/Sample Code/app/src/main/java/.../transport/
  # Android remains the producer and retains local rich FPV diagnostics
```

## Implementation Units

### U1 — Establish direct-runtime contract and migration guardrails

**Requirements:** R1, R3, R6; KTD1, KTD4.

**Approach:** Inventory the current Python receiver protocol/state/clock/evidence behavior and place the reusable, ROS-independent pieces under the bridge package’s installed scripts.  Remove the bridge’s `state_url`, polling timer, and relay-port assumptions only after equivalent direct ingress is in place.  Keep canonical packet schema/version validation, telemetry fragment completion, sequence handling, Android-to-edge monotonic mapping, and source-record publication behavior.  Make the direct configuration declare the five external ports and tablet clock host; never include `5602/5612` as operational inputs.

**Execution note:** Characterize fixtures first so migration preserves accepted/rejected packet behavior.

**Test scenarios:**
- Feed the existing valid telemetry, metadata, clock, and RTP fixtures and confirm the direct parser produces the same normalized records/state as the receiver.
- Send incomplete, duplicate, malformed, over-limit, and stale fragment samples; confirm they are rejected or held without replacing the last complete state.
- Verify no configuration or launch path binds a loopback relay port as a production video source.

### U2 — Implement direct UDP, clock, evidence, and ROS state publication

**Requirements:** R1, R2, R3, R5, R6; KTD1, KTD3, KTD4.

**Approach:** Create bounded UDP ingress endpoints for `5500`, `5501`, and `5502` inside the ROS runtime.  Ingest threads perform only receive, size check, parse, sequence/state update, timestamping, and non-blocking evidence enqueue; ROS callbacks publish completed state promptly.  Keep the clock service’s explicit tablet-IP probe and expose estimate quality rather than claiming latency when mapping is unavailable.  Publish raw source records, frame metadata, `NavigationState`, and standard diagnostics without a 10 Hz HTTP polling interval.

**Test scenarios:**
- Send a complete flight/RTK/gimbal sequence and assert exactly one source publication per new sequence plus a valid navigation message.
- Assert valid RTK selects `POSITION_RTK`; invalid/not-used RTK selects `POSITION_GPS_FALLBACK`; neither valid coordinate produces no false position.
- Simulate full evidence queue and writer error; assert ingress continues, counters increase, and diagnostics become degraded.
- Run clock probes with known synthetic offset and with no tablet response; assert estimate shown only in the former and failure remains non-fatal in the latter.

### U3 — Replace RTP relay with direct GStreamer input and measurements

**Requirements:** R1, R2, R5, R7; KTD1, KTD2, KTD3, KTD4.

**Approach:** Make one GStreamer pipeline per feed bind directly to `5600` or `5610`.  Add an input-boundary probe that validates RTP headers/payload type, tracks sequence dispositions, kernel receive overflow when supported, bytes, marker-delimited access units, RTP timestamps, bitrate, and FPS.  Branch the parsed stream to a leaky decoded appsink for ROS images and a configurable native display branch.  Start both preview branches by default and surface pipeline status/errors independently, even when FPV receives no packets or cannot negotiate.  Do not duplicate-bind sockets or re-send RTP through loopback.

Raw RTP capture is enabled only by the bench flag/config.  When enabled, enqueue the complete datagram with edge monotonic receive time before depayload; when disabled, retain only NDJSON metadata/counters.

**Test scenarios:**
- With a local valid RTP fixture, assert primary pipeline reaches decoded image publication, counter increments, and preview pipeline construction succeeds.
- Start both feeds with no traffic; assert both pipelines/windows are started or explicitly report startup failure without terminating telemetry ingress.
- Send malformed RTP, reordered packets, duplicates, gaps, and marker boundaries; assert counters distinguish each condition and no packet is silently labeled a decoded frame.
- Toggle raw capture during a controlled run; assert `.rtpbin` is created only while enabled, uses the documented timestamp/length framing, and NDJSON remains active in both modes.

### U4 — Move the measurement dashboard into the ROS runtime

**Requirements:** R4, R5, R6, R7; KTD1, KTD3, KTD4.

**Approach:** Migrate the existing local dashboard assets and bench logic behind a localhost server owned by the direct runtime.  Replace native-receiver HTTP state reads with the direct metric/state store.  Preserve editable tablet IP and runtime capture state in one operator-owned TOML file, validate before saving, and apply safe changes by controlled runtime restart where socket/pipeline recreation is required.  The UI shows copyable Ubuntu address, endpoint/port map, active configuration, data freshness, and explicit controls for raw RTP capture, bench duration, restart, and clean exit.  It opens only the browser dashboard; video stays in native GStreamer windows/RViz.

**Test scenarios:**
- Open the dashboard against a running fixture runtime and assert the displayed primary/FPV state, resolution/FPS, evidence health, Android health, and edge metrics correspond to the direct store.
- Select a tablet IP, save it, restart the runtime, and confirm the same value drives clock probes; reject an invalid IP without damaging the last valid file.
- Disable raw RTP capture in the UI, run a short stream, and assert no binary capture grows while telemetry/metadata NDJSON continues.
- Invoke dashboard exit and assert the HTTP server, ROS node, GStreamer pipelines, and browser/webview close cleanly.

### U5 — Deliver one Humble launch path while preserving mapper and RViz

**Requirements:** R2, R3, R7; KTD1, KTD2.

**Approach:** Replace the current bridge launcher with direct-ingress parameters and retain the combined bridge+mapper bringup.  Package static dashboard files and scripts correctly despite the package also generating `NavigationState`; avoid an `ament_python` package-name collision with generated interfaces.  Keep host networking and X11 forwarding in the existing Humble Compose service.  Change the host ergonomics so one documented `djiedge` command runs the direct bringup, opens the dashboard and both previews; retain a distinct shell/attach command for development instead of making the operational command an interactive shell.  Preserve RViz config/map assets and mapper launch behavior.

**Test scenarios:**
- Build with `colcon build --symlink-install` in the Humble container and source the installed workspace; launch direct bridge and combined bringup successfully without native `dji-edge` running.
- Verify `ros2 topic list` contains all intended direct topics plus mapper topics; verify a controlled navigation input produces pose/path updates.
- Verify X11 dashboard/browser and both GStreamer preview windows launch from the Docker command; a missing display produces an actionable diagnostic without losing ingress.
- Stop via Ctrl-C and via dashboard Exit; assert ports are released and a second launch succeeds.

### U6 — Validate parity, diagnose FPV, and retire the old operational path

**Requirements:** R1–R7; KTD1–KTD4.

**Approach:** Add an explicit bench procedure that captures a bounded session, reports configuration, Android pre-network counters, edge post-network counters, clock status, source/decoded frames, video resolution/FPS/bitrate, RTP gaps, evidence drops, and disk use.  Use it for primary parity first, then FPV.  FPV is only declared working when the direct secondary port receives packets, produces valid RTP/AUs, starts a decodable pipeline, and publishes images/preview.  After direct path passes the agreed hardware check, mark the native receiver, HTTP state API, relay ports, and old dashboard as deprecated and remove them in a separate, recoverable cleanup change.

**Test scenarios:**
- Bench a primary hardware session and confirm Android emitted RTP/AU counts and edge received RTP/AU counts are shown side by side with session/feed labels.
- Bench FPV with the Android fix; classify the first failing boundary (Android emits nothing, network/edge receives nothing, invalid RTP, no AU, parse/decode failure, or no decoded images) from recorded evidence.
- Compare direct runtime against the legacy path for the same controlled source; demonstrate no HTTP poll delay or loopback relay remains in the direct path.
- Run a long session with raw RTP off; verify NDJSON persists, evidence writer remains healthy, and disk growth excludes full video payloads.

## Dependency Order

```text
U1 contract/fixtures
 └─> U2 control ingress + state + evidence
      ├─> U3 direct RTP/GStreamer
      │    └─> U4 dashboard direct metrics
      └─> U5 launch/container/mapper integration
             └─> U6 hardware parity, FPV isolation, deprecation decision
```

## Verification Contract

### Automated gates

- Python protocol/state/evidence tests cover all five packet families and retain current fixtures.
- `colcon build --symlink-install` and `colcon test --event-handlers console_direct+` pass inside the Humble container.
- Launch smoke tests prove direct launch has no dependency on `127.0.0.1:8088`, `5602`, `5612`, or a native receiver process.
- Dashboard/API tests exercise valid and invalid config, capture mode, bench report, and clean exit.

### Hardware bench gate

Props-off, Android connected to the drone/Cendence, Ubuntu reachable from the tablet:

1. Start direct `djiedge`; verify dashboard, Primary preview, and FPV preview windows appear.
2. Confirm dashboard lists Ubuntu address and configured tablet IP, then Android transport targets that address.
3. Record a 60-second raw-RTP-on diagnostic session.  Save the generated bench report and evidence directory.
4. Check telemetry freshness, RTK/GPS fallback, compact Android health, received UDP/RTP, packet gaps, AU/FPS/bitrate, decoded image resolution/FPS, and clock-estimate validity.
5. Repeat with raw-RTP-off for a sustained session; confirm NDJSON continues and disk does not retain full RTP payloads.
6. For FPV, retain the direct evidence and classify the exact boundary rather than treating a missing window as a transport conclusion.

## Definition of Done

- One ROS 2 Humble direct edge launch receives the unchanged Android protocol and needs no native `dji-edge`, HTTP state server, or RTP relay ports.
- Primary and FPV have independently visible GStreamer preview startup, direct ROS image topics, and independently reported failures/metrics.
- Navigation/map path semantics remain intact: RTK preferred, GPS continuous fallback, relative aircraft altitude for local Z.
- The dashboard presents trustworthy boundary-labelled metrics and can safely edit tablet IP, control raw capture, restart, and exit.
- NDJSON is always recorded; raw RTP is opt-in; rosbag2 is documented separately.
- Automated build/tests and the hardware bench gate complete with an evidence-backed status for primary and FPV.
- Legacy receiver removal is performed only after parity evidence; until then it is retained as a rollback tool, not part of the direct operational command.

## Risks and Open Questions

- **FPV remains an Android-side unknown.** Direct edge metrics will locate it but cannot make absent/invalid secondary datagrams decodable.  Owner: Android transport agent for packet production; edge implementation for classification.
- **Native preview doubles decode work.** It is intentionally enabled for initial observability.  Measure CPU and disable only after FPV validation/RViz confidence.
- **Clock mapping is an estimate, not camera time.** Dashboard language must say callback-to-edge/transport estimate and hide it when mapping quality is insufficient.
- **Raw RTP can consume disk rapidly.** Bench capture must display state and destination, and default long sessions to off.
- **ROS-package Python layout requires care.** `dji_edge_bridge` already generates Python interfaces; implementation must not install a same-name `ament_python` module that collides with them.
