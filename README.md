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

## Start and stop

Load aliases once after a new terminal:

```bash
source ~/.zshrc
```

Start the full pipeline, including the dashboard, mapper, RViz-ready topics and both native GStreamer previews:

```bash
djiedge
```

It opens one Docker container named `dji_edge_humble_dev`, mounted at `/workspace`. It builds the root workspace, sources `install/setup.bash`, then launches driver and mapper. The terminal stays attached to the pipeline.

Open the dashboard at [http://127.0.0.1:8090](http://127.0.0.1:8090). Its **Exit** button stops the driver and the ROS launch then closes the mapper and container. `Ctrl-C` in the `djiedge` terminal has the same intent.

For a second shell while the pipeline is running:

```bash
djiedgeplus
```

For a shell without launching the pipeline:

```bash
djiedgeshell
```

`djiedgeplus` only works while `djiedge` or `djiedgeshell` is still running, because those commands use `--rm` and remove the container on exit.

## What the dashboard measures

The dashboard is a local measurement/status surface, not a video renderer. Video appears in the GStreamer native windows and can be viewed in RViz from:

- `/dji/primary/image_raw`
- `/dji/fpv/image_raw`

It reports post-network Edge evidence: accepted/rejected JSON packets, clock state, NDJSON writer health, RTP bytes/packets/gaps/duplicates, access units, estimated FPS/bitrate, decoded resolution and frame count. The Android `health` telemetry remains the separate pre-network source of callback, parser and sender metrics.

## ROS topic inventory

The driver publishes the following direct-ingress topics:

- Images: `/dji/primary/image_raw`, `/dji/fpv/image_raw` (`sensor_msgs/Image`, best-effort, depth 1).
- Raw Android records: `/dji/telemetry/flight`, `/dji/telemetry/rtk`, `/dji/telemetry/gimbal`, `/dji/telemetry/frame_metadata`, and `/dji/telemetry/video_access_unit` (`std_msgs/String` containing compact JSON).
- Navigation and health: `/dji/navigation/state` (`dji_edge_driver/NavigationState`), `/dji/diagnostics`, and `/dji/edge/transport_metrics` (`diagnostic_msgs/DiagnosticArray`).

The mapper consumes `/dji/navigation/state` and publishes `/map/cloud`, `/dji/navigation/pose`, `/dji/navigation/path`, `/dji/navigation/status`, and `/tf`.

## Configuration

The committed defaults are in [bridge.yaml](src/dji_edge_driver/config/bridge.yaml).

- `bind_host: "0.0.0.0"` listens on every local Ubuntu interface; it is not the tablet IP.
- `android_clock_host` is empty by default. Set it to the tablet IP only when its clock responder is enabled; traffic still works without it, but transport age is then unavailable.
- `preview_windows: true` starts Primary and FPV GStreamer windows.
- `capture_rtp: false` is the normal setting. NDJSON evidence is always on.

To keep local configuration out of Git, copy the file and pass it to launch from a shell:

```bash
cp src/dji_edge_driver/config/bridge.yaml bridge.local.yaml
```

Then use the Humble shell and pass `driver_config_file:=/workspace/bridge.local.yaml` to the launch command. The normal `djiedge` path currently uses the committed configuration directly.

## Evidence and recording

`evidence/` is created beside the workspace when the driver runs.

- NDJSON is always on: telemetry, frame metadata, clock exchanges and protocol errors are written asynchronously and do not block UDP ingress.
- Raw RTP is opt-in: set `capture_rtp: true` only for a short props-off diagnostic session. It creates `primary.rtpbin` and `fpv.rtpbin`, with `DJIRTP01` magic and a 4-byte big-endian length before each RTP datagram. It is packet evidence, not a video file or a rosbag.
- Use `rosbag2` separately when you need to replay ROS topics as a complete ROS session. It records published ROS messages, whereas raw RTP capture preserves the original post-network encoded datagrams for transport/H.264 investigation.

## Verification status

The Dockerized full bringup has been smoke-tested without the tablet: it builds `dji_edge_driver` and `dji_edge_mapper`, publishes the static map, starts the localizer and dashboard, and Exit shuts the whole process tree down cleanly. This does not replace a props-off tablet/drone bench: that bench must verify actual Android ingress, primary quality and the first failing FPV boundary.
