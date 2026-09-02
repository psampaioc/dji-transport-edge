cmake_minimum_required(VERSION 3.8)
project(dji_edge_bridge)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/NavigationState.msg"
  DEPENDENCIES std_msgs
)

ament_python_install_package(dji_edge_transport_core)

if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_transport_core test/test_transport_core.py)
endif()

install(PROGRAMS
  ${PROJECT_NAME}/bridge_node.py
  DESTINATION lib/${PROJECT_NAME}
  RENAME dji_edge_bridge
)

install(DIRECTORY launch config
  DESTINATION share/${PROJECT_NAME}
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
