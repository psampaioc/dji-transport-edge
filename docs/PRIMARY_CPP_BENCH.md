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
3. Open `http://127.0.0.1:8090`. It reports ingress bitrate/datagrams/bytes,
   decode and publish FPS/ages, superseded-frame rate, resolution, decoder,
   pipeline error and clock state. Saving the tablet clock IPv4 affects the
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
   If visible video freezes, name the first non-progressing boundary: Android
   sender health, Edge RTP ingress, H.264 decode, ROS publication, or RViz.
   A clock exchange is optional to video/navigation but, when configured, must
   show a positive successful sample count.

Frame contexts intentionally report `ASSOCIATION_UNAVAILABLE` in this build.
This is the safe result until a deterministic Android AU-to-decoded-frame key
is proven; it prevents an image from being given a false position.
