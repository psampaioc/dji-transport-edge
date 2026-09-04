# DJI Transport Edge

`DJI Transport Edge` is the Ubuntu-side ROS 2 Humble workspace for the M210 transport path.

```text
Android tablet
  UDP JSON 5500 / 5501 / 5502, RTP/H.264 5600 / 5610
                         │
                         ▼
dji_edge_driver ──► telemetry, NavigationState, diagnostics, Primary/FPV images
                         │
                         ▼
dji_edge_mapper ──► static PCD map, pose, path and TF ──► RViz
```

The workspace has exactly two packages:

- `dji_edge_driver`: owns direct Android ingress, clock mapping, NDJSON evidence, optional RTP capture, GStreamer decode, image topics, metrics and the local dashboard.
- `dji_edge_mapper`: owns the static map and WGS84-to-local projection. RTK is preferred; GPS is a continuous fallback. Z uses the aircraft-relative altitude.

The Android wire contract is [docs/PROTOCOL_V1.md](docs/PROTOCOL_V1.md). Do not change its ports or RTP payload type without measured Android/Edge evidence.

## Map privacy and local configuration

The public repository contains no point cloud and no geographic calibration. The real point cloud and its calibration remain local and ignored by Git.

This checkout already has `src/dji_edge_mapper/config/mapper.local.yaml`, which selects the private `map_vis.pcd` and `map_metadata.json`. The complete launch automatically prefers that ignored local configuration when it exists. A public clone starts the mapper disabled until its operator creates their own map:

```bash
cd src/dji_edge_mapper/config
cp mapper.local.yaml.example mapper.local.yaml
```

Then provide `map_vis.pcd`, `map_metadata.json`, and the correct projection/calibration in the ignored `mapper.local.yaml`. Without these files, the driver and dashboard still run, but no map, pose, path, or TF is published.

## Start and stop

Load aliases once after a new terminal:

```bash
source ~/.zshrc
```

Open the dedicated Humble container from anywhere:

```bash
djiedge
```

It opens one Docker container named `dji_edge_humble_dev`, mounted at `/workspace`. Inside it, build after source changes and launch the complete two-package stack:

```bash
colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
ros2 launch dji_edge_driver dji_edge_bringup.launch.py
```

The launch starts the driver, mapper, dashboard, both native GStreamer previews, and the preconfigured RViz view. RViz shows the Primary ROS image, the map, cyan continuous path, and yellow frame-synchronous pose. The terminal remains attached to the pipeline. `djiedge-run` is an optional host convenience command with the same build/source/launch sequence.

For headless Docker checks or a machine without X11, keep the exact same transport/map bringup and disable only RViz:

```bash
ros2 launch dji_edge_driver dji_edge_bringup.launch.py rviz:=false preview_windows:=false
```

Without an ignored local map configuration, RViz still opens and the driver/dashboard/video work, but the map/localization topics are intentionally disabled.

Open the dashboard at [http://127.0.0.1:8090](http://127.0.0.1:8090). Its **Exit** button stops the driver and the ROS launch then closes the mapper and container. `Ctrl-C` in the `djiedge` terminal has the same intent.

For a second shell while the pipeline is running:

```bash
djiedgeplus
```

`djiedgeplus` only works while `djiedge` is still running, because `djiedge` uses `--rm` and removes the container on exit.

## What the dashboard measures

The dashboard is a local measurement/status surface, not a video renderer. Video appears in the GStreamer native windows and can be viewed in RViz from:

- `/dji/primary/image_raw`
- `/dji/fpv/image_raw`

It reports post-network Edge evidence: accepted/rejected JSON packets, clock state, NDJSON writer health, RTP bytes/packets/gaps/duplicates, access units, estimated FPS/bitrate, decoded resolution and frame count. The Android `health` telemetry remains the separate pre-network source of callback, parser and sender metrics.

## ROS topic inventory

The driver publishes the following direct-ingress topics:

- Images: `/dji/primary/image_raw`, `/dji/fpv/image_raw` (`sensor_msgs/Image`, best-effort, depth 1), each with its matching `FrameContext` on `/dji/primary/frame_context` and `/dji/fpv/frame_context`.
- Navigation and health: `/dji/navigation/state` (`dji_edge_driver/NavigationState`), `/dji/diagnostics`, and `/dji/edge/transport_metrics` (`diagnostic_msgs/DiagnosticArray`). Raw Android packets remain evidence/dashboard diagnostics rather than ROS topics.

The mapper consumes navigation plus Primary frame context and publishes `/map/cloud`, the continuous `/dji/navigation/pose` and `/dji/navigation/path`, the frame-synchronous `/dji/frame/pose`, `/dji/navigation/status`, and `/tf`.

## Configuration

The committed defaults are in [bridge.yaml](src/dji_edge_driver/config/bridge.yaml).

- `bind_host: "0.0.0.0"` listens on every local Ubuntu interface; it is not the tablet IP.
- `android_clock_host` is empty by default. Set it to the tablet IP only when its clock responder is enabled; traffic still works without it, but transport age is then unavailable.
- `preview_windows: true` starts Primary and FPV GStreamer windows.
- `capture_rtp: false` is the normal setting. NDJSON evidence is always on.

The dashboard writes only `android_clock_host`, `capture_rtp`, and `preview_windows` to the ignored `/workspace/bridge.local.yaml`. It shows the local Ubuntu IPv4 addresses to copy into the tablet and never exposes ports, paths, shell commands, or flight controls. **Save** persists for the next launch; **Save and restart** persists then restarts the managed Edge stack once.

You can also create the same local configuration manually:

```bash
cp src/dji_edge_driver/config/bridge.yaml bridge.local.yaml
```

The normal package launcher automatically prefers this ignored file when it exists. Delete it to return to committed defaults.

`djiedge-run` uses this managed launcher, so dashboard **Save and restart** returns to the same command session after one clean shutdown. If launching manually inside `djiedge`, use `ros2 run dji_edge_driver dji_edge_bringup` rather than invoking `ros2 launch` directly.

## Evidence and recording

`evidence/edge-<UTC>-<id>/` is created beside the workspace for every driver run.

- NDJSON is always on: telemetry, frame metadata, frame-context associations, clock exchanges and protocol errors are written asynchronously and do not block UDP ingress. Android source times and Edge decode observations are separate fields.
- Raw RTP is opt-in: set `capture_rtp: true` only for a short props-off diagnostic session. It creates `primary.rtpbin` and `fpv.rtpbin` inside that same session, with `DJIRTP01` magic and a 4-byte big-endian length before each RTP datagram. It is packet evidence, not a video file or a rosbag.
- Use `rosbag2` separately when you need to replay ROS topics as a complete ROS session. It records published ROS messages, whereas raw RTP capture preserves the original post-network encoded datagrams for transport/H.264 investigation.

## Verification status

The Dockerized full bringup has been smoke-tested without the tablet: it builds `dji_edge_driver` and `dji_edge_mapper`, publishes the static map, starts the localizer and dashboard, and Exit shuts the whole process tree down cleanly.

A synthetic H.264/RTP Primary source was also received directly on UDP `5600`, decoded at `1280x720` and approximately `30 FPS`, and published as ROS images. Its old-frame counter increased under the synthetic producer, which proves the ROS handoff replaces stale frames instead of accumulating a queue.

This does not replace a props-off tablet/drone bench: that bench must verify actual Android ingress, Primary quality/latency, the first failing FPV boundary, GPS/RTK selection, and gimbal pitch.

The source-time association code has deterministic unit coverage. Its fully synthetic H.264/RTP GStreamer characterization is skipped in the current Humble image because it intentionally has no `x264enc` fixture encoder; the actual props-off bench remains the required proof that Android RTP timestamps map to decoded frames for both feeds.

## Props-off hardware bench

After the Android app connects to the drone and the Ubuntu network, its transport is enabled by default. Start the stack inside `djiedge` and run the following from a second container shell or with `djiedgeplus`:

```bash
python3 /workspace/src/dji_edge_driver/scripts/capture_transport_bench.py \
  --duration-s 60 \
  --out /workspace/.runtime/bench/transport-$(date -u +%Y%m%dT%H%M%SZ).json
```

The script only reads the local dashboard. The resulting JSON preserves each dashboard sample, including the exact evidence session path, pre-network Android health, post-network RTP/AU metrics, image publish/drop counters, clock state, navigation source, and both feeds. It does not capture raw RTP or issue any DJI command.
