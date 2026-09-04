---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
planning_depth: focused
title: Dashboard Local Configuration and Managed Restart - Plan
type: feat
date: 2026-09-04
---

# Dashboard Local Configuration and Managed Restart - Plan

## Goal Capsule

- **Objective:** An operator can change the tablet clock target, raw RTP capture, and preview setting locally in the dashboard, persist them safely, and restart the managed Edge stack once.
- **Means:** Validate a small allowlist, atomically write ignored `/workspace/bridge.local.yaml`, set a bounded restart-request marker, and let the existing package launcher consume that marker after its child launch exits.
- **Authority:** Ports, RTP PT, bind host, map paths, shell commands, and all flight actions remain immutable/unavailable in the UI.
- **Stop condition:** The dashboard never executes a command, accepts arbitrary paths, or itself spawns ROS/container processes.

## Requirements

- R1. Persist only `android_clock_host`, `capture_rtp`, and `preview_windows`; validate IPv4/hostname and booleans strictly.
- R2. Write local YAML atomically and leave the existing active file unchanged on validation/write failure.
- R3. Report active configuration source, local Ubuntu addresses, and raw-capture status.
- R4. `Save and restart` only requests one restart; the launcher restarts only after a clean full-stack exit and removes the marker before relaunch.
- R5. Normal Exit remains terminal and does not restart.
- R6. A public clone has no generated local file and continues using committed defaults.

## Implementation Units

### U1. Build local configuration boundary

- **Files:** `dji_edge_transport_core/local_config.py`, tests, `.gitignore`.
- **Approach:** Validate the allowlist and atomically serialize a complete ROS parameter overlay; no generic YAML editor or arbitrary file input.
- **Verification:** Valid input round-trips; invalid host/bool and simulated replace failure preserve prior content.

### U2. Expose safe dashboard API and controls

- **Files:** `dashboard.py`, `dji_edge_driver_node.py`, tests.
- **Approach:** Add local-only GET/POST config endpoints, minimal form and distinct save versus save-and-restart state. The node owns a callback that requests its own orderly stop after a successful restart request.
- **Verification:** HTTP tests prove rejection leaves state unchanged and restart callback fires only after a valid save.

### U3. Supervise only requested restarts

- **Files:** `dji_edge_bringup.launch.py`, `dji_edge_bringup.sh`, README, launch tests.
- **Approach:** Prefer ignored `/workspace/bridge.local.yaml` when present. Replace `exec` launcher with a loop that restarts only when an exact marker exists after launch exits; ordinary exit ends normally.
- **Verification:** Headless Docker smoke proves one stack starts; shell-level marker test proves marker is consumed once without duplicates.

## Verification Contract

- Docker Humble builds and test suites pass.
- HTTP/config tests cover validation, atomic failure, and restart request isolation.
- Headless bringup remains usable; public config/map behavior remains unchanged.
- Final props-off operator test verifies dashboard save/restart against live Android ingress.

## Definition of Done

- Dashboard edits only the allowed local values, clearly reports source/status, and cannot alter transport ports or invoke commands.
- Valid Save and restart persists then produces exactly one managed relaunch; Exit remains terminal.
- All non-hardware tests pass and hardware proof is explicitly deferred.
