---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
planning_depth: focused
title: RViz Operator Bringup - Plan
type: feat
date: 2026-09-04
---

# RViz Operator Bringup - Plan

## Goal Capsule

- **Objective:** One supported Edge bringup command opens the configured RViz map/path/current-frame view and Primary ROS image for an operator.
- **Means:** Extend the existing complete launch with an explicit `rviz` argument and launch `rviz2` only when enabled.
- **Authority:** Preserve two packages, direct Android ingress, one GStreamer decode per feed, bounded image handoff, map privacy, and headless Docker verification.
- **Stop condition:** No new decoder, relay, recording path, map asset, or automatic vehicle action is introduced.

## Product Contract

### Requirements

- R1. The existing complete launch starts driver and mapper exactly as today, then starts `rviz2` with the installed mapper RViz configuration when `rviz:=true`.
- R2. `rviz:=false` is a supported headless mode and does not require X11/RViz.
- R3. RViz consumes only `/dji/primary/image_raw`, `/dji/navigation/path`, `/dji/frame/pose`, map and TF topics already published by the two packages.
- R4. A public clone without ignored map/calibration still starts driver/dashboard and RViz cleanly; map/localization remain disabled by existing configuration.
- R5. Documentation shows the operator command, headless command, expected views, and X11 limitation.

### Key Decisions

- **Explicit RViz opt-in:** `rviz:=true` is the operator default while `rviz:=false` keeps CI and non-X11 environments reliable.
- **Frame pose over latest pose:** RViz highlights `/dji/frame/pose` as the image-correlated marker; the continuous path remains separate.

### Scope Boundaries

- No dashboard controls, Android changes, GStreamer changes, third package, map/calibration content, or video processing changes.

## Implementation Units

### U1. Add explicit RViz launch control

- **Files:** `src/dji_edge_driver/launch/dji_edge_bringup.launch.py`, `src/dji_edge_driver/test/test_bringup_launch.py`.
- **Approach:** Declare `rviz` (default true) and conditionally launch `rviz2` from `dji_edge_mapper`'s installed `.rviz` file. Keep existing driver/mapper includes and their configuration selection unchanged.
- **Test scenarios:** Default launch describes one RViz process; `rviz:=false` has none; driver and mapper are still present in either mode.
- **Verification:** Launch-description test passes without DISPLAY or map data.

### U2. Document the settled operator path

- **Files:** `README.md`.
- **Approach:** Replace the inaccurate statement that the current launch opens RViz with exact commands and expected behavior; describe frame-synchronous yellow pose versus cyan trajectory.
- **Verification:** Documentation paths match installed launch/config filenames.

## Verification Contract

| Gate | Proof |
| --- | --- |
| Launch contract | Unit test inspects default and headless launch descriptions. |
| Regression | Docker Humble builds both packages and runs all tests with `rviz:=false` capability covered. |
| Operator value | One documented command launches RViz without adding a processing stage. |
| Hardware boundary | Props-off test later confirms Primary appears in RViz; no hardware claim from this plan. |

## Definition of Done

- `ros2 launch dji_edge_driver dji_edge_bringup.launch.py` opens the configured RViz session in an X11-capable operator container.
- `rviz:=false` remains reliable for headless Docker use.
- RViz shows existing map/path/image topics and the distinct frame-synchronous pose.
- Build/tests/documentation pass, while the final visual Primary proof remains explicitly props-off hardware work.
