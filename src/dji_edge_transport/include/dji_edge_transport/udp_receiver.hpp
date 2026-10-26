#pragma once

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace dji_edge_transport {

struct UdpPeer {
  std::string ipv4;
  std::uint16_t port{0};
};

class UdpReceiver {
public:
  using Callback = std::function<void(const std::vector<std::uint8_t> &, std::uint64_t, const UdpPeer &)>;

  UdpReceiver(std::string bind_host, std::uint16_t port, std::size_t max_bytes, Callback callback);
  ~UdpReceiver();
  UdpReceiver(const UdpReceiver &) = delete;
  UdpReceiver & operator=(const UdpReceiver &) = delete;
  bool start();
  void stop();
  bool ready() const;
  std::string last_error() const;
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
  mutable std::mutex state_mutex_;
  std::condition_variable ready_cv_;
  bool start_complete_{false};
  bool ready_{false};
  std::string last_error_;
  std::thread thread_;
};

}  // namespace dji_edge_transport
