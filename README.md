# DJI Transport Edge

Ubuntu/ROS 2 Humble workspace that receives the DJI Android transport and publishes ROS. It never controls flight, gimbal, takeoff, landing or missions.

## Active architecture

```text
Android tablet
  ├─ telemetry JSON 5500, AU metadata 5501, clock 5502
  └─ Primary H.264/RTP 5600
             ↓
dji_edge_transport: one C++ ROS node
  ├─ one headless GStreamer pipeline
  ├─ NVIDIA decode when the running container proves it is available
  ├─ one newest-frame handoff and one ROS delivery worker
  ├─ /dji/primary/image_raw, navigation, frame context, status and diagnostics
  └─ optional loopback dashboard at http://127.0.0.1:8090
             ↓
dji_edge_mapper: static PCD map, pose, path and RViz
```

The workspace contains `dji_edge_transport` and `dji_edge_mapper`. [docs/PROTOCOL_V1.md](docs/PROTOCOL_V1.md) owns Android ports, compact JSON, RTP/H.264 and original Android/DJI timestamps. Primary only is supported; no Edge FPV listener exists.

## Start

Inside the project Humble container:

```bash
source /opt/ros/humble/setup.bash
cd /workspace
colcon build --packages-select dji_edge_transport dji_edge_mapper --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
source install/setup.bash
ros2 launch dji_edge_mapper dji_edge_bringup.launch.py
```

Use `rviz:=false` for a headless bench. The dashboard starts automatically when 8090 is free but is optional: video, ROS and mapping continue if it is closed or unavailable. It shows distinct Android-local sender health, Edge metadata/RTP/decode/publication, clock and bounded evidence state. It only persists `android_clock_host` for the next launch.

## Video latency policy

- One Primary pipeline ends at `appsink max-buffers=1 drop=true`.
- One pending decoded image is retained; a new image replaces an older pending image.
- One internal ROS delivery worker is woken by decode. There is no frame polling timer and no ROS publish in the GStreamer streaming callback.
- There is no preview branch, relay, FFmpeg, `shmsink`, automatic decoder restart or default raw RTP capture.
- After real UDP loss, H.264 waits for the next sender IDR. The Edge cannot reconstruct missing packets.

`sensor_msgs/Image` needs CPU-accessible pixels. The application performs one managed copy from the decoded GStreamer buffer into ROS image storage and moves that storage to the message. GPU-native ROS image transport is outside this path.

## Decoder preflight

Run in the exact container that launches the stack:

```bash
nvidia-smi
gst-inspect-1.0 nvh264dec
gst-inspect-1.0 avdec_h264
```

`nvidia` is reported only after `nvh264dec` exists and its pipeline enters PLAYING. Otherwise `avdec_h264` is used with a fallback reason. CPU operation is supported.

## Bench and map configuration

Follow [docs/PRIMARY_CPP_BENCH.md](docs/PRIMARY_CPP_BENCH.md) for a 60-second props-off run. Its result must identify the first stopped observable boundary: Android-local sender counters, Edge RTP ingress, H.264 decode, ROS publication or RViz-only rendering. It must not call an Android-to-Edge gap Wi-Fi loss without direct proof. Change Android resolution, bitrate, FPS or IDR cadence only from that evidence.

Map assets/calibration are local and ignored by Git. Create `src/dji_edge_mapper/config/mapper.local.yaml` from its example and place the matching map files beside it. Without those assets transport still works, but the mapper cannot publish a real map/path.
