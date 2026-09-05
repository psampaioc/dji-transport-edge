# Edge Transport Scope

This repository is the Ubuntu/ROS 2 Edge side of the DJI transport system.

- Work only in this repository unless the user explicitly asks to change another project.
- Do not edit, build, install, or operate `/home/psampaioc/Workspaces/matrice-dji-sdk_v4` from work here.
- Treat the Android application as an external wire-contract owner. Preserve its UDP ports, RTP payload contract, and source-time fields unless the user explicitly authorizes a coordinated protocol change.
- Use the project Humble Docker runtime for ROS builds and tests. Keep the two-package workspace (`dji_edge_driver`, `dji_edge_mapper`) and avoid an additional relay or decoder without evidence that it is required.
- Keep flight, mission, gimbal, and takeoff/landing actions out of scope. Edge only receives, measures, records, and publishes ROS topics.
