# ROS 2 Humble bridge specification

## Purpose

Publish trustworthy navigation, raw normalized telemetry and decoded camera
frames to ROS 2 while preserving the existing edge receiver as the UDP/RTP
validation, clock and evidence boundary.

## Packages

- `dji_edge_bridge`: owns `NavigationState`, the state adapter, raw telemetry
  publishers, RTP-to-image GStreamer appsinks and diagnostics.
- `dji_edge_mapper`: retained map asset, cloud publisher and path/TF projector.

## Quality and timestamps

`NavigationState.header.stamp` is bridge publication time.  Android and edge
monotonic timestamps are carried separately and must not be represented as ROS
wall-clock time.  RTK is selected only when the fusion coordinate is finite and
the SDK reports it being used.  GPS remains valid fallback; the path never stops
solely because RTK disappears.

## Failure behavior

- State endpoint unavailable: retain no stale navigation sample, emit WARN
  diagnostics and recover on the next poll.
- RTP missing: image publisher simply has no new images; telemetry continues.
- FPV absent: the FPV pipeline remains idle without affecting primary video.
- Out-of-map/jump samples: mapper rejects them and exposes rejection counts.

## Deliberate exclusions

No flight control, DJI SDK, OCR telemetry, SHM video transport or raw RTP
recording is added here.  Evidence capture remains configurable in the native
edge receiver.
