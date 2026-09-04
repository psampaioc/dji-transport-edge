#include "dji_edge_mapper/map_publisher.hpp"

int main(int argc, char** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<dji_edge_mapper::MapPublisher>());
  rclcpp::shutdown();
  return 0;
}
