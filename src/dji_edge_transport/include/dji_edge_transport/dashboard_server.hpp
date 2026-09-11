#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

namespace dji_edge_transport {

class DashboardServer {
public:
  using StateProvider = std::function<std::string()>;
  DashboardServer(std::uint16_t port, std::string local_config_path, StateProvider state_provider);
  ~DashboardServer();
  DashboardServer(const DashboardServer &) = delete;
  DashboardServer & operator=(const DashboardServer &) = delete;
  bool start();
  void stop();
  std::string error() const;

private:
  void run();
  std::uint16_t port_;
  std::string local_config_path_;
  StateProvider state_provider_;
  std::atomic<bool> stopping_{false};
  int socket_{-1};
  std::thread thread_;
  mutable std::mutex mutex_;
  std::string error_;
};

}  // namespace dji_edge_transport
