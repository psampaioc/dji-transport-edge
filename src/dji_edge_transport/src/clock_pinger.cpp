#include "dji_edge_transport/clock_pinger.hpp"

#include <arpa/inet.h>
#include <chrono>
#include <cstring>
#include <nlohmann/json.hpp>
#include <sys/socket.h>
#include <unistd.h>

namespace dji_edge_transport {
namespace {
std::uint64_t now_ns()
{
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count());
}
}

ClockPinger::ClockPinger(std::string target_ipv4, std::uint16_t target_port, std::string session,
  double interval_s, double timeout_s, std::size_t max_bytes, Callback callback)
: target_ipv4_(std::move(target_ipv4)), target_port_(target_port), session_(std::move(session)),
  interval_s_(interval_s), timeout_s_(timeout_s), max_bytes_(max_bytes), callback_(std::move(callback)) {}

ClockPinger::~ClockPinger() { stop(); }

void ClockPinger::start()
{
  if (target_ipv4_.empty() || thread_.joinable()) return;
  stopping_.store(false);
  thread_ = std::thread(&ClockPinger::run, this);
}

void ClockPinger::stop()
{
  stopping_.store(true);
  if (thread_.joinable()) thread_.join();
}

std::string ClockPinger::last_error() const
{
  std::lock_guard<std::mutex> lock(error_mutex_);
  return last_error_;
}

void ClockPinger::run()
{
  while (!stopping_.load()) {
    exchange_once();
    const auto end = std::chrono::steady_clock::now() + std::chrono::duration<double>(interval_s_);
    while (!stopping_.load() && std::chrono::steady_clock::now() < end) {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
  }
}

bool ClockPinger::exchange_once()
{
  ++attempts_;
  const int socket_fd = ::socket(AF_INET, SOCK_DGRAM, 0);
  if (socket_fd < 0) return false;
  sockaddr_in target{};
  target.sin_family = AF_INET;
  target.sin_port = htons(target_port_);
  if (inet_pton(AF_INET, target_ipv4_.c_str(), &target.sin_addr) != 1) {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = "clock target must be an IPv4 literal";
    close(socket_fd);
    return false;
  }
  const auto t0 = now_ns();
  const auto packet = nlohmann::json{{"v", 1}, {"type", "clock_ping"},
      {"session", session_}, {"stream", "clock"}, {"seq", ++sequence_},
      {"t0_edge_send_mono_ns", t0}}.dump();
  if (sendto(socket_fd, packet.data(), packet.size(), 0,
      reinterpret_cast<const sockaddr *>(&target), sizeof(target)) < 0) {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = std::strerror(errno);
    close(socket_fd);
    return false;
  }
  timeval timeout{};
  timeout.tv_sec = static_cast<time_t>(timeout_s_);
  timeout.tv_usec = static_cast<suseconds_t>((timeout_s_ - timeout.tv_sec) * 1'000'000.0);
  setsockopt(socket_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  std::vector<std::uint8_t> bytes(max_bytes_ + 1);
  const auto received = recvfrom(socket_fd, bytes.data(), bytes.size(), 0, nullptr, nullptr);
  const auto t3 = now_ns();
  close(socket_fd);
  if (received < 0) {
    ++timeouts_;
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = "clock timeout";
    return false;
  }
  std::string error;
  bytes.resize(static_cast<std::size_t>(received));
  const auto pong = decode_clock(bytes, max_bytes_, error);
  if (!pong || !pong->is_pong || pong->t0_edge_send_mono_ns != t0) {
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = pong ? "unmatched clock pong" : error;
    return false;
  }
  ++successes_;
  if (callback_) callback_(*pong, t3);
  return true;
}

}  // namespace dji_edge_transport
