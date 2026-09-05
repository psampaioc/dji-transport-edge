#include "dji_edge_mapper/map_publisher.hpp"
#include <pcl/io/pcd_io.h>
#include <ament_index_cpp/get_package_share_directory.hpp>

#include <filesystem>

namespace
{

std::string resolveMapperAsset(const std::string& configured_path)
{
  if (configured_path.empty() || configured_path.front() == '/') {
    return configured_path;
  }

  const auto package_path = std::filesystem::path(
    ament_index_cpp::get_package_share_directory("dji_edge_mapper")) / configured_path;
  if (std::filesystem::exists(package_path)) {
    return package_path.string();
  }

  const auto workspace_path = std::filesystem::path("/workspace/src/dji_edge_mapper") / configured_path;
  if (std::filesystem::exists(workspace_path)) {
    return workspace_path.string();
  }
  return package_path.string();
}

}  // namespace

namespace dji_edge_mapper
{

MapPublisher::MapPublisher(const rclcpp::NodeOptions& options) : Node("map_publisher", options)
{
  // Declare parameters
  this->declare_parameter<bool>("enabled", false);
  this->declare_parameter<std::string>("pcd_file_path", "config/map_vis.pcd");
  this->declare_parameter<std::string>("map_frame", "map");
  this->declare_parameter<std::string>("topic_name", "/map/cloud");
  this->declare_parameter<bool>("publish_once", true);

  // Get parameters
  pcd_file_path_ = this->get_parameter("pcd_file_path").as_string();
  map_frame_ = this->get_parameter("map_frame").as_string();
  topic_name_ = this->get_parameter("topic_name").as_string();
  publish_once_ = this->get_parameter("publish_once").as_bool();

  if (!this->get_parameter("enabled").as_bool()) {
    RCLCPP_WARN(this->get_logger(), "Map publisher disabled: create config/mapper.local.yaml and provide a site map.");
    return;
  }

  // Public assets resolve from the installed package. Ignored map assets remain source-local.
  pcd_file_path_ = resolveMapperAsset(pcd_file_path_);

  RCLCPP_INFO(this->get_logger(), "Loading map from: %s", pcd_file_path_.c_str());
  RCLCPP_INFO(this->get_logger(), "Publishing on topic: %s (frame: %s)", topic_name_.c_str(), map_frame_.c_str());

  // Latched QoS: transient_local + reliable + depth=1
  rclcpp::QoS qos(1);
  qos.reliable();
  qos.transient_local();
  qos.keep_last(1);

  map_pub_ = this->create_publisher<sensor_msgs::msg::PointCloud2>(topic_name_, qos);

  // Load and publish
  loadAndPublishMap();
}

void MapPublisher::loadAndPublishMap()
{
  pcl::PointCloud<pcl::PointXYZRGB>::Ptr cloud(new pcl::PointCloud<pcl::PointXYZRGB>);

  if (pcl::io::loadPCDFile<pcl::PointXYZRGB>(pcd_file_path_, *cloud) == -1) {
    RCLCPP_ERROR(this->get_logger(), "Failed to load PCD file: %s", pcd_file_path_.c_str());
    return;
  }

  RCLCPP_INFO(this->get_logger(), "Loaded map with %zu points", cloud->size());

  sensor_msgs::msg::PointCloud2 cloud_msg;
  pcl::toROSMsg(*cloud, cloud_msg);
  cloud_msg.header.frame_id = map_frame_;
  cloud_msg.header.stamp = rclcpp::Time(0);

  map_pub_->publish(cloud_msg);
  RCLCPP_INFO(this->get_logger(), "Published map point cloud (%zu points) on %s", cloud->size(), topic_name_.c_str());

  if (publish_once_) {
    // Keep spinning to maintain latched topic, but we're done publishing
    RCLCPP_INFO(this->get_logger(), "Published once (latched). Node will stay alive to serve late subscribers.");
  }
}

}  // namespace dji_edge_mapper

#include <rclcpp_components/register_node_macro.hpp>
RCLCPP_COMPONENTS_REGISTER_NODE(dji_edge_mapper::MapPublisher)
