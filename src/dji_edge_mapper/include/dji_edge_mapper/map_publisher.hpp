#ifndef DJI_EDGE_MAPPER__MAP_PUBLISHER_HPP_
#define DJI_EDGE_MAPPER__MAP_PUBLISHER_HPP_

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include <pcl_conversions/pcl_conversions.h>

namespace dji_edge_mapper
{

class MapPublisher : public rclcpp::Node
{
public:
  explicit MapPublisher(const rclcpp::NodeOptions& options = rclcpp::NodeOptions());

private:
  void loadAndPublishMap();

  rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr map_pub_;
  std::string pcd_file_path_;
  std::string map_frame_;
  std::string topic_name_;
  bool publish_once_;
};

}  // namespace dji_edge_mapper

#endif  // DJI_EDGE_MAPPER__MAP_PUBLISHER_HPP_
