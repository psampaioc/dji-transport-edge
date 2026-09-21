# Primary C++ props-off bench

This proves the `dji_edge_transport` path.

1. Put the tablet and Ubuntu on the same network. In the Android transport UI,
   set the Ubuntu IPv4 destination and enable transport. No flight command is
   involved.
2. From the project Humble container, build then launch the new two-package
   stack:

   ```bash
   source /opt/ros/humble/setup.bash
   cd /workspace
   colcon build --packages-select dji_edge_transport dji_edge_mapper --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release
   source install/setup.bash
   ros2 launch dji_edge_mapper dji_edge_bringup.launch.py
   ```

   Add `rviz:=false` for a headless bench.
3. Open `http://127.0.0.1:8090`. It separately reports Android-local health,
   metadata receipt, RTP ingress bitrate/datagrams/bytes, RTP header continuity,
   decode and publish FPS/ages, superseded-frame rate, resolution, decoder,
   bounded evidence and clock state. Saving the tablet clock IPv4 affects the
   next launch; it never restarts the stack automatically.
4. For at least 60 seconds, verify all of the following advance or remain
   healthy:

   ```bash
   ros2 topic hz /dji/primary/image_raw
   ros2 topic echo --once /dji/edge/transport_status
   ros2 topic echo --once /dji/navigation/state
   ros2 topic echo --once /dji/navigation/path
   ```

5. Record the dashboard state and terminal output. Success requires Primary
   RTP ingress, decoded and published image counts, a nonzero resolution,
   current image age, continuous navigation/path output, and a normal mapper.
   If visible video freezes, name the first non-progressing boundary: Android-
   local sender health, Edge RTP ingress, H.264 decode, ROS publication, or
   RViz. Fresh Android health with stale Edge RTP is an explicitly qualified
   ingress gap, not proven Wi-Fi loss.
   A clock exchange is optional to video/navigation but, when configured, must
   show a positive successful sample count.

6. Run a controlled, props-off interruption: disable Android transport (or
   isolate the receiver) briefly, then restore it. The active session evidence
   directory under `/workspace/.runtime/evidence/<session>/` must contain one
   `primary_incident` and, after a new sender IDR decodes and publishes, one
   `primary_recovery`. Do not call this proven Wi-Fi loss merely because the
   Android health counter is fresh.
7. Restart Android transport once. Confirm the dashboard session changes and
   that new health/metadata counters advance without retaining telemetry from
   the former session.
8. Run a ten-minute real-network soak. Save the final dashboard state and the
   session NDJSON. The result passes only if Primary stays current, or every
   interruption is represented by bounded incident/recovery evidence with no
   queue growth, disk runaway, dashboard disruption or unreported pipeline
   error.

Frame contexts intentionally report `ASSOCIATION_UNAVAILABLE` in this build.
This is the safe result until a deterministic Android AU-to-decoded-frame key
is proven; it prevents an image from being given a false position.
