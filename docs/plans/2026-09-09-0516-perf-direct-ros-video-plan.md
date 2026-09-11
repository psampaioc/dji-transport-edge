---
title: Direct ROS Video Stability Plan
type: perf
date: 2026-09-09
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

## Goal Capsule

- **Objective:** An operator receives the newest decodable Primary and FPV image on the existing ROS topics without an Edge-created backlog or recovery loop turning ordinary UDP loss into repeated freezes.
- **Means:** Keep one bounded GStreamer pipeline per feed, use NVIDIA when runtime-proven, remove automatic pipeline reconstruction, and keep detailed RTP accounting out of the normal Python hot path. (KTD1, KTD2, KTD3)
- **Authority:** `docs/PROTOCOL_V1.md` owns Android ports, H.264/RTP form, logical feeds, and Android/DJI source times. This plan does not alter that contract.
- **Execution profile:** Characterization tests first, then the Humble build/test gates, then a props-off bench only when Android is emitting.
- **Stop conditions:** Do not change ports, packetization, Android telemetry, timestamp semantics, mapper/map behavior, or manual-flight boundaries. Stop if implementation needs a new Android timestamp contract or the tablet is not emitting.
- **Tail ownership:** `ce-work` owns implementation and verification; the operator owns encoder/keyframe and Wi-Fi changes.

---

## Product Contract

### Summary

The normal route is one direct chain per feed: Android RTP/UDP enters GStreamer in `dji_edge_driver`, is decoded once, retains only the newest decoded frame, and publishes to the existing ROS topic with best-effort depth one.

Packet loss must not cause Edge to rebuild the chain. The feed waits for a future Android IDR/keyframe and exposes that state honestly.

### Problem Frame

`FeedPipeline.maintain()` currently detects stalled decode and repeatedly rebuilds the pipeline. A new depayloader cannot decode until another IDR arrives, so restart-on-stall causes the observed loop: short smooth period, freeze, jump to current frame, repeat.

The capture-off normal route also installs two Python RTP pad probes. The source probe performs detailed packet metrics even when capture is disabled; the post-jitter probe is required by the existing fail-closed PTS-to-RTP context binding. The dashboard therefore pays for packet-level detail that does not help the operator decide between no ingress, waiting for a keyframe, decode progress, or ROS publication.

### Requirements

**Direct video behavior**

- R1. Preserve Android JSON/RTP ports, PT `96`, existing Primary/FPV ROS topics, and original Android/DJI timestamp semantics.
- R2. Run one independent headless GStreamer pipeline per logical feed, ending at exactly one `appsink`; no normal preview renderer, tee, relay, shared-memory sink, raw-capture writer, or second consumer.
- R3. Retain newest-frame behavior: `appsink max-buffers=1 drop=true`, one-slot Edge handoff, and ROS `BEST_EFFORT` depth `1`.
- R4. When RTP is fresh but decode has no current frame, report a waiting-for-keyframe/decode state and leave the pipeline alive. Do not automatically tear down/recreate it.
- R5. A GStreamer `ERROR` or `EOS` affects only its own feed. FPV absence/failure must not stop, recreate, or delay Primary.

**Normal-path observability**

- R6. With `capture_rtp=false`, remove detailed source RTP packet metrics: sequence gaps, duplicate/out-of-order counts, bitrate, and access-unit totals. Keep lightweight facts: age of the most recently post-jitter RTP identity observation, decoded/published/old-frame-drop counts, resolution, decoded/output FPS, backend/reason, and pipeline error/status. This is a feed-progress signal, not a physical UDP-loss measurement.
- R7. Preserve the existing post-jitter PTS identity observation only for fail-closed `FrameContext`; it must use read-only mapped data, never clone payload bytes, and never gate image publication.
- R8. Raw RTP capture remains explicit and props-off-only. It may attach a source probe and copy packet bytes into `DJIRTP01` evidence only while enabled; that probe must not exist when capture is off.
- R9. Dashboard, diagnostics, NDJSON, and documentation distinguish no ingress, waiting for a keyframe, feed error, and active decode. They do not imply packet recovery or publish packet metrics no longer collected.

**Upstream network boundary**

- R10. Treat UDP loss as upstream. H.264 recovery requires a future Android IDR; document an Android keyframe interval of approximately 0.5–1 second and Wi-Fi capacity for the selected bitrate.
- R11. Android encoder settings, Wi-Fi/AP setup, mapper, RViz, and source-time association redesign are outside this plan.

### Success Criteria

- A transient RTP loss followed by a sender IDR resumes in the existing pipeline without an automatic Edge restart.
- Default Primary/FPV graphs each have one `appsink` and no source capture probe.
- A slow ROS consumer gets the newest available frame or drops it; image age does not grow as a backlog.
- FPV failures do not change Primary state.
- The dashboard tells an operator whether to check tablet emission, await an IDR, or investigate an actual feed error.

### Scope Boundaries

- No Android code is changed in this repository.
- No new ROS node, HTTP relay, FFmpeg route, native preview, `shmsink`, or zero-copy image transport is introduced.
- The PTS-to-RTP association is not made stronger here. It remains fail-closed; a stronger access-unit association requires separate Android/Edge evidence.
- The final decoded GPU-to-CPU/Python `sensor_msgs/Image` copy remains unavoidable in the current Python publisher. This plan removes extra packet work around it.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **No automatic decode-stall restart.** `(session-settled: user-directed — chosen over watchdog pipeline reconstruction: packet loss must wait for an IDR rather than create a recovery loop.)` Remove the watchdog restart state machine. Bus errors remain per-feed errors. Governs R4, R5, R10.
- KTD2. **At most one normal Python RTP identity probe.** `(session-settled: user-directed — chosen over two per-packet metric probes: preserve fail-closed FrameContext evidence, but remove detailed metrics and capture-off packet copies.)` Remove the source metrics probe; retain only post-jitter read-only identity work until the source-time contract is redesigned. Governs R6, R7, R8, R9.
- KTD3. **Newest frame wins.** `(session-settled: user-directed — chosen over queued delivery: low latency and current imagery are more valuable than retaining every frame.)` Preserve one-buffer `appsink`, one-slot handoff, and depth-one best-effort publication. Governs R2, R3.
- KTD4. **Loss recovery belongs upstream.** `(session-settled: user-directed — chosen over Edge-side packet recovery: the Edge cannot reconstruct H.264 RTP datagrams that never arrived.)` Surface the boundary and document sender IDR/link requirements. Governs R9, R10, R11.

### High-Level Technical Design

```mermaid
flowchart TB
  T[Android tablet] -->|RTP H264 primary 5600| P[Primary FeedPipeline]
  T -->|RTP H264 fpv 5610| F[FPV FeedPipeline]
  T -->|JSON 5500 5501 5502| J[Existing JSON ingress]
  P --> D1[NVIDIA decoder or CPU fallback]
  F --> D2[NVIDIA decoder or CPU fallback]
  D1 --> A1[one-frame appsink]
  D2 --> A2[one-frame appsink]
  A1 --> R1[/dji/primary/image_raw]
  A2 --> R2[/dji/fpv/image_raw]
  J --> N[/dji/navigation/state]
  P -. read-only identity only .-> C[Fail-closed FrameContext]
  F -. read-only identity only .-> C
```

The feed state is intentionally small: `waiting_for_rtp`, `waiting_for_keyframe`, `running`, and per-feed `pipeline_error`. `waiting_for_keyframe` does not call `set_state(NULL)`, clear the feed, or construct a new decoder.

### Current-State Findings

- `src/dji_edge_driver/src/dji_edge_transport_core/feed_pipeline.py` already builds the desired single headless graph for `preview_windows=false`, with NVIDIA runtime selection and CPU fallback.
- It still installs `_on_rtp` at the UDP source and `_on_jitter_rtp` post-jitter. `_on_rtp` drives `RtpMetrics` even when `RawRtpCapture` is disabled.
- `FeedPipeline.maintain()` is invoked every 0.25 seconds from `src/dji_edge_driver/src/dji_edge_driver_node.py`; successful restarts do not reset the stale decoded-progress condition, allowing a restart storm.
- `VideoFeed` instances are already independent and `publish_latest()` is already image-first with fail-closed context. Preserve both properties.
- `src/dji_edge_driver/src/dji_edge_transport_core/rtp.py` owns obsolete normal-path gap/duplicate/AU/bitrate calculations.
- `docs/PROTOCOL_V1.md` describes automatic recreation and must be corrected.

### Assumptions and Dependencies

- The normal Humble container runtime already reports NVIDIA decoding; retain its runtime probe and CPU fallback.
- `rtph264depay wait-for-keyframe=true` can recover only if Android emits SPS/PPS/IDR at a useful cadence. Edge cannot impose that cadence.
- After removal of packet-gap counters, Android health counters plus Edge post-jitter feed freshness are the supported evidence for upstream no-emission/decoding boundaries. They do not quantify physical UDP loss.

### Sequencing

Remove the restart loop first, then simplify capture-off RTP observation, then update status/contract documentation. Each step is testable independently.

---

## Implementation Units

### U1. Remove automatic restart ownership from a feed

- **Goal:** Let a stalled H.264 feed wait for the next keyframe instead of rebuilding itself in a loop.
- **Requirements:** R4, R5, R10.
- **Files:** `src/dji_edge_driver/src/dji_edge_transport_core/feed_pipeline.py`, `src/dji_edge_driver/src/dji_edge_driver_node.py`, `src/dji_edge_driver/test/test_transport_core.py`, `src/dji_edge_driver/test/test_bringup_launch.py`.
- **Approach:** Delete restart state, backoff constants, restart methods, and the maintenance timer/call path that only supports automatic reconstruction. Retain bus monitoring and intentional-close behavior. Derive `waiting_for_keyframe` from recent ingress plus stale/absent decoded progress.
- **Test scenarios:** Fresh RTP/no decode reports waiting without teardown; a decoded frame changes it to running; primary error does not mutate FPV; FPV error does not affect Primary counters/latest frame/status; intentional close does not create an error.
- **Verification:** In a controlled source interruption, confirm no restart count or `set_state(NULL)` after a decode stall; recovery happens only after a future sender IDR.

### U2. Make capture-off RTP observation lightweight

- **Goal:** Remove detailed metrics and the duplicate source probe from the normal route.
- **Requirements:** R6, R7, R8, R9.
- **Files:** `src/dji_edge_driver/src/dji_edge_transport_core/feed_pipeline.py`, `src/dji_edge_driver/src/dji_edge_transport_core/rtp.py`, `src/dji_edge_driver/src/dji_edge_driver_node.py`, `src/dji_edge_driver/test/test_transport_core.py`, `src/dji_edge_driver/test/test_gstreamer_pts.py`.
- **Approach:** Replace normal `RtpMetrics` with a minimal feed-progress snapshot derived from decode/output and the already-required post-jitter identity path. Attach a source capture probe only when raw capture is enabled; it owns the packet copy. Do not add another ordinary probe.
- **Test scenarios:** Capture-off has no source probe and does not call raw capture; capture-on writes the current length-prefixed evidence; identity uses read-only mapped data; malformed RTP does not crash a feed; missing/ambiguous identity leaves context unavailable.
- **Verification:** Static audit finds no capture-off `extract_dup`, source probe, packet-gap, bitrate, or AU normal-state field. A short capture-on bench still writes valid `DJIRTP01` evidence.

### U3. Make dashboard and diagnostics current-frame oriented

- **Goal:** Expose the live failure boundary without expensive or misleading packet detail.
- **Requirements:** R6, R9, R10.
- **Files:** `src/dji_edge_driver/src/dji_edge_driver_node.py`, `src/dji_edge_driver/src/dji_edge_transport_core/feed_pipeline.py`, `src/dji_edge_driver/src/dji_edge_transport_core/dashboard.py`, `src/dji_edge_driver/test/test_transport_core.py`.
- **Approach:** Report last ingress/decode ages, decoded/published/old-frame-drop counts, resolution, bounded decoded/output FPS, decoder backend/reason, capture state, and feed error. Remove restart/gap/bitrate/AU labels. Dashboard remains an observer and cannot affect pipeline threads.
- **Test scenarios:** State distinguishes no ingress, waiting for keyframe, running, and feed-specific error; Primary stays healthy when FPV is not emitted; unknown resolution/FPS serializes safely; dashboard failure isolation remains covered.
- **Verification:** During a tablet bench the dashboard alone identifies Android not sending, Edge waiting for an IDR, decoding/publishing, or a feed-local error.

### U4. Align the contract explanation and field instructions

- **Goal:** Stop operators from expecting an Edge restart to recover missing UDP.
- **Requirements:** R1, R8, R9, R10, R11.
- **Files:** `docs/PROTOCOL_V1.md`, `README.md`, `src/dji_edge_driver/config/bridge.yaml`, `src/dji_edge_driver/test/test_bringup_launch.py`.
- **Approach:** Keep ports/defaults unchanged. Replace recreation wording with waiting-for-IDR; explain normal lightweight status versus raw capture; document the upstream keyframe cadence/link-capacity requirement. Explicitly state Android changes are not implemented here.
- **Test scenarios:** Config/launch retain `preview_windows=false` and `capture_rtp=false`; docs contain no normal-path restart or gap-metric claim.
- **Verification:** A new operator can choose the next action from dashboard status: check tablet emission/destination, await/check IDR, or investigate a feed error.

---

## Verification Contract

| Gate | Applies to | Evidence of success |
| --- | --- | --- |
| Characterization tests | U1-U3 | Lifecycle, one-frame handoff, context, capture, and status tests state the no-restart contract before code changes. |
| Driver tests | U1-U4 | Humble-container `colcon test --packages-select dji_edge_driver` and `colcon test-result --verbose` pass. |
| Static hot-path audit | U1-U2 | Capture-off graph has no tee/render/fake sink, source RTP probe, `extract_dup`, restart state machine, or maintenance timer. |
| Feed-isolation smoke | U1-U3 | A fixture error in FPV leaves Primary pipeline object, latest frame, counters, and state unchanged; reciprocal case also holds. |
| Headless smoke | U1-U4 | Normal bringup starts headless and shuts down cleanly without publisher-context errors. |
| Props-off Primary bench | U1-U4 | At least 60 seconds of ingress/decode/publish progress, resolution, bounded output FPS, decoder backend, and no restart loop. |
| Controlled loss/keyframe bench | U1, U4 | A safe interruption leaves the pipeline alive; it resumes only on a future Android IDR. If IDR cannot be proven, record that result rather than infer. |
| FPV boundary bench | U1-U3 | Emitted FPV is measured independently; absent FPV reports no ingress without reducing Primary health. |

The performance acceptance is newest-frame behavior rather than an invented latency target: active decode has no growing backlog; loss reports waiting rather than rebuilding; and the normal Python route does no detailed RTP metric work beyond the retained source-time identity observation.

---

## Definition of Done

- Automatic decode-stall restart, backoff, counters, and the maintenance timer are removed from normal feed lifecycle.
- Primary and FPV remain isolated pipelines with separate status and errors.
- Capture-off video has one headless output branch and one newest-frame ROS handoff per feed.
- Gap, bitrate, and AU metrics no longer execute or appear in normal live state; raw RTP capture remains explicit and faithful when enabled.
- `FrameContext` remains fail-closed and never blocks image publication or replaces Android/DJI source time.
- Dashboard, diagnostics, NDJSON, `README.md`, and `docs/PROTOCOL_V1.md` explain no-ingress, waiting-for-keyframe, and feed error correctly.
- Android contract, telemetry/navigation/map behavior, ports, and manual-flight safety boundaries remain unchanged.
- Humble build/tests, headless smoke, feed-isolation coverage, and a props-off bench pass; abandoned restart/metric code and tests are removed.

---

## Appendix

### Sources and Evidence

- Feed lifecycle and pipeline: `src/dji_edge_driver/src/dji_edge_transport_core/feed_pipeline.py`.
- ROS one-frame publication and former maintenance timers: `src/dji_edge_driver/src/dji_edge_driver_node.py`.
- Test seams: `src/dji_edge_driver/test/test_transport_core.py`, `src/dji_edge_driver/test/test_gstreamer_pts.py`, and `src/dji_edge_driver/test/test_bringup_launch.py`.
- Android contract and obsolete restart wording: `docs/PROTOCOL_V1.md`.
- Live evidence on 2026-09-09: NVIDIA decoding was selected, but repeated watchdog reconstruction coincided with stalled decode; later all Android ingress stopped together. This separates Edge lifecycle correction from upstream sender/link diagnosis.
