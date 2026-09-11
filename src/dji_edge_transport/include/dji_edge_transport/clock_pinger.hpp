#pragma once

#include "dji_edge_transport/protocol_decoder.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

namespace dji_edge_transport {

class ClockPinger {
public:
  using Callback = std::function<void(const ClockPacket &, std::uint64_t)>;

  ClockPinger(std::string target_ipv4, std::uint16_t target_port, std::string session,
    double interval_s, double timeout_s, std::size_t max_bytes, Callback callback);
  ~ClockPinger();
  ClockPinger(const ClockPinger &) = delete;
  ClockPinger & operator=(const ClockPinger &) = delete;
  void start();
  void stop();
  std::uint64_t attempts() const { return attempts_.load(); }
  std::uint64_t successes() const { return successes_.load(); }
  std::uint64_t timeouts() const { return timeouts_.load(); }
  std::string last_error() const;

private:
  void run();
  bool exchange_once();
  std::string target_ipv4_;
  std::uint16_t target_port_;
  std::string session_;
  double interval_s_;
  double timeout_s_;
  std::size_t max_bytes_;
  Callback callback_;
  std::atomic<bool> stopping_{false};
  std::atomic<std::uint64_t> sequence_{0};
  std::atomic<std::uint64_t> attempts_{0};
  std::atomic<std::uint64_t> successes_{0};
  std::atomic<std::uint64_t> timeouts_{0};
  std::thread thread_;
  mutable std::mutex error_mutex_;
  std::string last_error_;
};

}  // namespace dji_edge_transport
