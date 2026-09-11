# Future TODO

This backlog is intentionally outside the current Edge preflight and navigation-rate plan. Do not fold these items into the four limited fixes without a separate plan and evidence.

## Frame truth and video transport

1. Replace or prove the current PTS-to-RTP binding with an access-unit association that can preserve Android/DJI source time without associating a displayed frame to the wrong position.
2. Run a real tablet H.264 decode bench for NVIDIA selection. Factory availability and pipeline construction are not proof that the decoder handles this stream on the GPU.
3. Measure and remove remaining avoidable RTP payload copies when `capture_rtp=false`. The unavoidable normal `sensor_msgs/Image` path still copies decoded pixels into CPU/ROS memory and serializes them for an external RViz subscriber. Evaluate GPU-native image transport or a composed C++ processing pipeline only after a measured baseline proves that cost matters. Preserve raw RTP capture as an explicit bench-only recording mode.

## Driver and mapper correctness

4. Correct the `rejected_outside_map` accounting so map-bound rejection increments the diagnostic counter.
5. Make the private map-asset mount dependency explicit. Keep private assets out of the public package while ensuring a clean workspace cannot silently use stale installed map files.
6. Consolidate driver configuration ownership. The local runtime overlay currently replaces the package YAML, so defaults must not drift between Python and YAML.
7. Review the unused ROS component registration and remove it only if no current launcher or consumer requires it.

## Operational cleanup and reliability

8. Retire old `config.toml`, legacy evidence formats, stale plan references, and obsolete scripts only after a clean build and live tablet bench prove they are unused.
9. Add a measured DDS/network tuning pass only after the direct video path is stable. Keep any interface or QoS change evidence-led and separate from Android RTP diagnosis.
10. Revisit the dashboard/restart lifecycle boundaries after transport timing is proven. Dashboard failure must remain non-fatal and configuration changes must not create multiple supervisors.
