---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
implementation_status: completed
---

# ROS 2 Humble Workspace Refactor Plan

**Target workspace:** `ros2_ws/`

## Goal Capsule

**Objective:** An operator can start the Humble ROS environment through one familiar alias, build artifacts into the edge workspace itself, and bring up the ROS bridge plus map/path pipeline with one launch command.

**Means:** Normalize the workspace to two ROS 2 packages and make the Docker image dependency-only, with `ros2_ws/` bind-mounted as `/workspace`. (KTD1, KTD2)

**Authority:** User request and existing edge-transport behavior take precedence. Preserve the current topic names, RTK-preferred/GPS-fallback navigation behavior, map assets, and the native edge receiver/dashboard.

**Stop conditions:** Do not move the Android transport, native `dji-edge` receiver, or point-cloud source workspace. Do not add flight-control behavior.

---

## Product Contract

### Summary

The initial ROS addition works but has an unnecessarily split interface package and creates its build artifacts inside the Docker image. Refactor it into a conventional ROS 2 workspace whose generated artifacts live under `ros2_ws/` on the host, matching the user's existing Docker workflow.

### Problem Frame

The current Docker image contains a prebuilt workspace, so entering its Compose service does not visibly establish `ros2_ws/build`, `install`, and `log` on the host. The hidden Compose profile also makes the service look absent from normal inventory. Three packages are more separation than this internal integration needs.

### Requirements

- **R1.** The workspace must have only `src/dji_edge_bridge` and `src/dji_edge_mapper` as source packages; generated `build/`, `install/`, and `log/` must be created at the `ros2_ws/` root by normal `colcon build`.
- **R2.** `dji_edge_bridge` must own both `NavigationState.msg` and the ROS bridge node while preserving the existing bridge topic contract.
- **R3.** `dji_edge_mapper` must retain map publication, WGS84-to-local-map projection, pose, path, TF, and RTK/GPS quality semantics.
- **R4.** A single bring-up launch file must start bridge and mapper. Individual bridge/mapper launch files remain available for diagnosis.
- **R5.** The Humble image must contain OS/ROS/GStreamer dependencies only; it must not copy or build the project workspace during image creation.
- **R6.** The Compose service must bind-mount `ros2_ws/` as the container workspace, use host networking, remain explicit to start, and be visible in ordinary Compose inventory.
- **R7.** Add a `djiedge` alias following the existing `rosstudy`/`dronevision` interactive-container pattern, with an optional attach alias only if the running-container workflow remains useful.
- **R8.** Documentation must state which process remains native (`dji-edge`) and which runs in Docker (ROS bridge/mapper/RViz), including the single normal bring-up path.

### Success Criteria

- A fresh `ros2_ws/` has only `src/`, docs, and ignored generated-artifact paths before the first build.
- Starting `djiedge` bind-mounts the host workspace at `/workspace`; a build produces `ros2_ws/build`, `ros2_ws/install`, and `ros2_ws/log` on the host.
- `ros2 launch dji_edge_bridge dji_edge_bringup.launch.py` exposes the existing ROS topics and map path pipeline.
- Existing primary video, future FPV video, telemetry, navigation and map topic names are unchanged.

### Scope Boundaries

In scope: package merge, ROS-standard layout, launch consolidation, Docker/Compose mount behavior, aliases, and documentation.

Out of scope: Android FPV repair, live hardware validation, changing RTP protocol/ports, moving the native edge receiver into Docker, map regeneration, or new detection nodes.

---

## Planning Contract

### Key Technical Decisions

- **KTD1 — Merge interface and bridge into one `ament_cmake` package** (session-settled: user-directed — chosen over a reusable standalone interface package because this message is internal to this two-package workspace). `dji_edge_bridge` generates its message and installs its Python node script directly through CMake. This avoids a Python module-name collision with the generated `dji_edge_bridge.msg` package while retaining valid ROS metadata.
- **KTD2 — Docker image supplies dependencies; the bind-mounted workspace supplies source and artifacts** (session-settled: user-directed — chosen over image-time workspace builds so artifacts are visible and persistent in the project directory). The Compose mount is `ros2_ws/ → /workspace`, and the working directory is `/workspace`.
- **KTD3 — Retain two domain packages but one normal launcher.** `dji_edge_mapper` remains independently testable and reusable with simulated navigation; `dji_edge_bridge` owns transport concerns. `dji_edge_bringup.launch.py` is the ordinary operator entry point.
- **KTD4 — Keep `dji-edge` native for this refactor.** It remains the UDP/RTP validation, clock, evidence and dashboard process. The Dockerized ROS nodes consume its local HTTP/RTP boundary. This avoids changing a hardware-proven path while the ROS workspace is normalized.
- **KTD5 — Alias names mirror current local convention.** `djiedge` opens the Humble Compose shell with X11 access; `djiedgeplus` attaches only while that named shell container exists.

### Target Structure

```text
ros2_ws/
├── src/
│   ├── dji_edge_bridge/
│   │   ├── dji_edge_bridge/
│   │   ├── launch/
│   │   ├── msg/
│   │   ├── resource/
│   │   ├── config/
│   │   ├── CMakeLists.txt
│   │   └── package.xml
│   └── dji_edge_mapper/
│       ├── config/              # map_vis.pcd and map metadata
│       ├── include/
│       ├── launch/
│       ├── rviz/
│       ├── scripts/
│       ├── src/
│       ├── CMakeLists.txt
│       └── package.xml
├── build/                       # generated, ignored
├── install/                     # generated, ignored
├── log/                         # generated, ignored
├── docs/
└── .gitignore
```

### Runtime Shape

```text
tablet → native dji-edge receiver ──HTTP/RTP──> Docker Humble: dji_edge_bridge
                                                    │
                              /dji/navigation/state + images + raw telemetry
                                                    │
                                                dji_edge_mapper
                                                    │
                                  /map/cloud, pose, path, TF
```

### Implementation Constraints

- Create the replacement package with `ros2 pkg create --build-type ament_cmake`; do not hand-create an ad-hoc package skeleton.
- Preserve QoS and current topic names unless a test exposes a concrete defect.
- Do not delete original source packages until the replacement has built and passed the named smoke checks; only then remove the superseded copies within `ros2_ws/src/`.
- Keep `build/`, `install/`, `log/`, Python cache and local generated configuration ignored. Do not ignore required `map_vis.pcd` or `map_metadata.json` in this workspace.

---

## Implementation Units

### U1. Normalize the ROS workspace and merge the bridge package

**Goal:** Replace `dji_edge_msgs` and `dji_edge_ros_bridge` with ROS-standard `dji_edge_bridge` created by the ROS package tool.

**Requirements:** R1, R2.

**Dependencies:** None.

**Files:** `src/dji_edge_bridge/`, `src/dji_edge_msgs/`, `src/dji_edge_ros_bridge/`, `.gitignore`.

**Approach:**

1. Generate the CMake package skeleton inside `src/` using the Humble container and `ros2 pkg create`.
2. Move the message definition, bridge Python module, launch and configuration into it.
3. Configure one package to generate the message and install the Python executable through `ament_cmake_python`.
4. Update imports/dependencies and remove only the two superseded source directories after the replacement passes build and interface discovery.

**Patterns to follow:** ROS Humble package creation and generated-interface metadata; existing mapper CMake package layout.

**Test scenarios:**

- `NavigationState` is discoverable after sourcing `install/setup.bash`.
- The bridge executable is discoverable and starts without a live receiver.
- A missing state endpoint emits bounded diagnostics, not a crash or 10 Hz log flood.

**Verification:** `colcon build --symlink-install` succeeds from `ros2_ws/`; only the two intended source packages appear in `src/`.

### U2. Add one operator bring-up launcher while retaining diagnostic launchers

**Goal:** Give the operator one launch command for the normal path without losing isolated debugging entry points.

**Requirements:** R3, R4.

**Dependencies:** U1.

**Files:** `src/dji_edge_bridge/launch/dji_edge_bringup.launch.py`, `src/dji_edge_bridge/launch/`, `src/dji_edge_mapper/launch/`.

**Approach:**

1. Add a top-level launch that includes the bridge and mapper launches.
2. Expose configuration-file launch arguments rather than duplicating parameters.
3. Keep `dji_edge_bridge.launch.py` and `dji_edge_mapper.launch.py` for independent testing.

**Test scenarios:**

- Bring-up resolves both packages and starts both nodes.
- Bridge-only and mapper-only launches remain callable.
- Map publisher loads the copied one-million-point `map_vis.pcd` and stays latched.

**Verification:** A normal bring-up advertises navigation, image, telemetry, map, pose and path topics; map publishing works without tablet data.

### U3. Make the Humble Docker workflow workspace-mounted and explicit

**Goal:** Make the host's `ros2_ws/` the visible, persistent colcon workspace for interactive Docker work.

**Requirements:** R1, R5, R6.

**Dependencies:** U1.

**Files:** `../Dockerfiles/humble_docker/Dockerfile.dji_sdk`, `../Dockerfiles/docker-compose.yml`, `ros2_ws/README.md`.

**Approach:**

1. Remove project source copying and image-time `colcon build` from the Dockerfile; retain only Humble, GStreamer, PCL, RViz and build dependencies.
2. Configure the Compose service without the hidden profile, binding the exact `ros2_ws/` path to `/workspace` and setting that as working directory.
3. Keep `network_mode: host` for the receiver's loopback HTTP/RTP and ROS DDS; retain X11 mount only for RViz.
4. Use environment interpolation with a safe default project path so the service works from any shell location and can be overridden on another machine.

**Execution note:** This is packaging/runtime work; prefer Compose validation plus an actual bind-mount smoke test over unit tests.

**Test scenarios:**

- Normal Compose inventory displays the DJI Humble service without activating a profile.
- Entering the service exposes `/workspace/src` from the host.
- A clean host workspace build creates `build/`, `install/`, and `log/` under `ros2_ws/`, not only inside the image.
- The container sees the native edge receiver at `127.0.0.1:8088` under host networking.

**Verification:** Rebuild image succeeds; a container-started build and source step use artifacts on the host bind mount.

### U4. Add the user-facing Docker aliases and unify documentation

**Goal:** Make the Humble environment start as naturally as the user's current ROS aliases and accurately describe process ownership.

**Requirements:** R7, R8.

**Dependencies:** U2, U3.

**Files:** `~/.zshrc` (operator shell configuration), `GUIA_DE_UTILIZACAO.md`, `ros2_ws/README.md`, `ros2_ws/docs/ROS2_BRIDGE_SPEC.md`.

**Approach:**

1. Add `djiedge` for the interactive Compose shell and `djiedgeplus` for attaching to its named, still-running shell container.
2. Document the sequence: native `dji-edge` first; `djiedge`; workspace build/source; one bring-up launch; topic/RViz checks.
3. State that closing the Docker shell ends that `--rm` container and that artifacts remain in the mounted host workspace.

**Test scenarios:**

- A fresh zsh resolves `djiedge` and opens the intended Compose service.
- The documentation has one normal-start path and labels bridge-only/mapper-only commands as diagnostic alternatives.
- No documentation claims FPV works until packets reach the secondary RTP port.

**Verification:** Alias expansion, Compose service name, mounted working directory and documentation commands agree exactly.

---

## Verification Contract

- Build from the bind-mounted `ros2_ws/` using Humble and source its generated `install/setup.bash`.
- Verify package inventory, message interface, bridge executable, mapper executable and the unified launcher.
- Run the map publisher smoke test against `config/map_vis.pcd` and verify one million points load.
- Start the bridge with the receiver stopped and verify controlled diagnostics; then repeat with receiver/tablet transport active and inspect navigation/image topic rates.
- Check that host `ros2_ws/build`, `install`, and `log` exist after the container build and remain ignored.
- Run `docker compose config --quiet` and confirm the service is visible without profile selection.

## Definition of Done

- Exactly two source packages remain: `dji_edge_bridge` and `dji_edge_mapper`.
- The standard ROS workspace artifact layout is produced on the host through the mounted container.
- `djiedge` starts the correct Humble environment from any shell directory.
- One bring-up launcher starts bridge and mapper; isolated launchers remain usable.
- Existing edge behavior and ROS topic names are preserved.
- Docker image, workspace build, map smoke test and hardware-ready bridge smoke test pass.
- Superseded package files and documentation claims are removed; no abandoned package directory remains in `src/`.

## Sources

- ROS 2 Humble documentation: package creation places packages under `src/`; generated interfaces are produced from `.msg` through `rosidl`; `colcon build` creates the normal workspace install surface.
- Existing local patterns: `~/.zshrc`, `../Dockerfiles/docker-compose.yml`, and the `video_ws/V2` and `conectivity_ws` workspace layouts.
