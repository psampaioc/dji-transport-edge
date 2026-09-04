cmake_minimum_required(VERSION 3.8)
project(dji_edge_driver)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/NavigationState.msg"
  "msg/FrameContext.msg"
  DEPENDENCIES std_msgs
)

ament_python_install_package(dji_edge_transport_core)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_transport_core test/test_transport_core.py)
  ament_add_pytest_test(test_gstreamer_pts test/test_gstreamer_pts.py)
  ament_add_pytest_test(test_bringup_launch test/test_bringup_launch.py)
endif()

install(PROGRAMS
  scripts/dji_edge_driver_node.py
  DESTINATION lib/${PROJECT_NAME}
  RENAME dji_edge_driver
)

install(PROGRAMS scripts/dji_edge_bringup.sh
  DESTINATION lib/${PROJECT_NAME}
)

install(DIRECTORY launch config
  DESTINATION share/${PROJECT_NAME}
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
