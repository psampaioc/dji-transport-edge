#include "dji_edge_transport/transport_node.hpp"

#include <rclcpp/rclcpp.hpp>

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<dji_edge_transport::TransportNode>());
  rclcpp::shutdown();
  return 0;
}
