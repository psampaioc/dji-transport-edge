cmake_minimum_required(VERSION 3.16)
project(dji_edge_transport)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Wpedantic)

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_gtest REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(PkgConfig REQUIRED)
find_package(nlohmann_json 3 REQUIRED)
pkg_check_modules(GST REQUIRED IMPORTED_TARGET gstreamer-1.0 gstreamer-app-1.0)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/TransportStatus.msg"
  "msg/NavigationState.msg"
  "msg/FrameContext.msg"
  DEPENDENCIES std_msgs
)

add_library(dji_edge_transport_core
  src/transport_node.cpp
  src/protocol_decoder.cpp
  src/source_time_state.cpp
  src/clock_mapper.cpp
  src/clock_pinger.cpp
  src/dashboard_server.cpp
  src/udp_receiver.cpp
  src/frame_handoff.cpp
  src/frame_context_builder.cpp
  src/primary_video_pipeline.cpp
)
target_include_directories(dji_edge_transport_core PUBLIC include)
target_link_libraries(dji_edge_transport_core PkgConfig::GST nlohmann_json::nlohmann_json)
ament_target_dependencies(dji_edge_transport_core
  rclcpp sensor_msgs std_msgs diagnostic_msgs
)
rosidl_get_typesupport_target(cpp_typesupport_target ${PROJECT_NAME} "rosidl_typesupport_cpp")
target_link_libraries(dji_edge_transport_core "${cpp_typesupport_target}")

add_executable(dji_edge_transport_node src/main.cpp)
target_link_libraries(dji_edge_transport_node dji_edge_transport_core)
ament_target_dependencies(dji_edge_transport_node rclcpp)

install(TARGETS dji_edge_transport_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY launch/ DESTINATION share/${PROJECT_NAME}/launch)
install(DIRECTORY config DESTINATION share/${PROJECT_NAME})

if(BUILD_TESTING)
  ament_add_gtest(test_protocol_decoder test/test_protocol_decoder.cpp)
  target_include_directories(test_protocol_decoder PRIVATE include)
  target_link_libraries(test_protocol_decoder dji_edge_transport_core)
  ament_target_dependencies(test_protocol_decoder rclcpp)
  ament_add_gtest(test_frame_handoff test/test_frame_handoff.cpp)
  target_include_directories(test_frame_handoff PRIVATE include)
  target_link_libraries(test_frame_handoff dji_edge_transport_core)
  ament_add_gtest(test_frame_context_builder test/test_frame_context_builder.cpp)
  target_include_directories(test_frame_context_builder PRIVATE include)
  target_link_libraries(test_frame_context_builder dji_edge_transport_core)
  ament_add_gtest(test_transport_node test/test_transport_node.cpp)
  target_include_directories(test_transport_node PRIVATE include)
  target_link_libraries(test_transport_node dji_edge_transport_core)
  ament_target_dependencies(test_transport_node rclcpp)
  ament_add_gtest(test_source_time_state test/test_source_time_state.cpp)
  target_include_directories(test_source_time_state PRIVATE include)
  target_link_libraries(test_source_time_state dji_edge_transport_core)
  ament_add_gtest(test_clock_mapper test/test_clock_mapper.cpp)
  target_include_directories(test_clock_mapper PRIVATE include)
  target_link_libraries(test_clock_mapper dji_edge_transport_core)
endif()

ament_export_dependencies(rosidl_default_runtime)
ament_package()
