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
  // Performs one bounded request/reply exchange. Public for deterministic local
  // socket tests; normal runtime use remains start()/stop().
  bool exchange_once();
  std::uint64_t attempts() const { return attempts_.load(); }
  std::uint64_t successes() const { return successes_.load(); }
  std::uint64_t timeouts() const { return timeouts_.load(); }
  std::uint64_t send_failures() const { return send_failures_.load(); }
  std::uint64_t receive_failures() const { return receive_failures_.load(); }
  std::uint64_t decode_failures() const { return decode_failures_.load(); }
  std::uint64_t unmatched_pongs() const { return unmatched_pongs_.load(); }
  std::uint64_t last_success_mono_ns() const { return last_success_mono_ns_.load(); }
  std::uint64_t last_rtt_ns() const { return last_rtt_ns_.load(); }
  std::string last_error() const;

private:
  void run();
  bool exchange_on_socket(int socket_fd);
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
  std::atomic<std::uint64_t> send_failures_{0};
  std::atomic<std::uint64_t> receive_failures_{0};
  std::atomic<std::uint64_t> decode_failures_{0};
  std::atomic<std::uint64_t> unmatched_pongs_{0};
  std::atomic<std::uint64_t> last_success_mono_ns_{0};
  std::atomic<std::uint64_t> last_rtt_ns_{0};
  std::thread thread_;
  mutable std::mutex error_mutex_;
  std::string last_error_;
};

}  // namespace dji_edge_transport
