#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <string>
#include <thread>
#include <vector>

namespace dji_edge_transport {

class UdpReceiver {
public:
  using Callback = std::function<void(const std::vector<std::uint8_t> &, std::uint64_t)>;

  UdpReceiver(std::string bind_host, std::uint16_t port, std::size_t max_bytes, Callback callback);
  ~UdpReceiver();
  UdpReceiver(const UdpReceiver &) = delete;
  UdpReceiver & operator=(const UdpReceiver &) = delete;
  void start();
  void stop();
  std::uint64_t received() const { return received_.load(); }
  std::uint64_t rejected() const { return rejected_.load(); }
  std::uint64_t last_receive_mono_ns() const { return last_receive_mono_ns_.load(); }

private:
  void run();
  std::string bind_host_;
  std::uint16_t port_;
  std::size_t max_bytes_;
  Callback callback_;
  std::atomic<bool> stopping_{false};
  std::atomic<std::uint64_t> received_{0};
  std::atomic<std::uint64_t> rejected_{0};
  std::atomic<std::uint64_t> last_receive_mono_ns_{0};
  int socket_{-1};
  std::thread thread_;
};

}  // namespace dji_edge_transport
