#include "dji_edge_transport/clock_pinger.hpp"

#include <arpa/inet.h>
#include <algorithm>
#include <chrono>
#include <cstring>
#include <nlohmann/json.hpp>
#include <poll.h>
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
  const int socket_fd = ::socket(AF_INET, SOCK_DGRAM, 0);
  if (socket_fd < 0) {
    ++send_failures_;
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = std::strerror(errno);
    return;
  }
  while (!stopping_.load()) {
    exchange_on_socket(socket_fd);
    const auto end = std::chrono::steady_clock::now() + std::chrono::duration<double>(interval_s_);
    while (!stopping_.load() && std::chrono::steady_clock::now() < end) {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
  }
  close(socket_fd);
}

bool ClockPinger::exchange_once()
{
  const int socket_fd = ::socket(AF_INET, SOCK_DGRAM, 0);
  if (socket_fd < 0) {
    ++send_failures_;
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = std::strerror(errno);
    return false;
  }
  const bool result = exchange_on_socket(socket_fd);
  close(socket_fd);
  return result;
}

bool ClockPinger::exchange_on_socket(int socket_fd)
{
  ++attempts_;
  sockaddr_in target{};
  target.sin_family = AF_INET;
  target.sin_port = htons(target_port_);
  if (inet_pton(AF_INET, target_ipv4_.c_str(), &target.sin_addr) != 1) {
    ++send_failures_;
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = "clock target must be an IPv4 literal";
    return false;
  }
  const auto t0 = now_ns();
  const auto sequence = ++sequence_;
  const auto packet = nlohmann::json{{"v", 1}, {"type", "clock_ping"},
      {"session", session_}, {"stream", "clock"}, {"seq", sequence},
      {"t0_edge_send_mono_ns", t0}}.dump();
  if (sendto(socket_fd, packet.data(), packet.size(), 0,
      reinterpret_cast<const sockaddr *>(&target), sizeof(target)) < 0) {
    ++send_failures_;
    std::lock_guard<std::mutex> lock(error_mutex_);
    last_error_ = std::strerror(errno);
    return false;
  }
  std::vector<std::uint8_t> bytes(max_bytes_ + 1);
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::duration<double>(timeout_s_);
  while (true) {
    const auto remaining = deadline - std::chrono::steady_clock::now();
    if (remaining <= std::chrono::steady_clock::duration::zero()) {
      ++timeouts_;
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = "clock timeout";
      return false;
    }
    const auto timeout_ms = static_cast<int>(std::chrono::ceil<std::chrono::milliseconds>(remaining).count());
    pollfd descriptor{socket_fd, POLLIN, 0};
    const auto polled = poll(&descriptor, 1, std::max(timeout_ms, 1));
    if (polled == 0) continue;
    if (polled < 0) {
      if (errno == EINTR) continue;
      ++receive_failures_;
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = std::string("poll: ") + std::strerror(errno);
      return false;
    }
    if ((descriptor.revents & (POLLERR | POLLNVAL)) != 0) {
      ++receive_failures_;
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = "clock socket poll error";
      return false;
    }
    sockaddr_in peer{};
    socklen_t peer_size = sizeof(peer);
    const auto received = recvfrom(socket_fd, bytes.data(), bytes.size(), 0,
      reinterpret_cast<sockaddr *>(&peer), &peer_size);
    if (received < 0) {
      if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
      ++receive_failures_;
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = std::string("recvfrom: ") + std::strerror(errno);
      return false;
    }
    if (static_cast<std::size_t>(received) > max_bytes_) {
      ++decode_failures_;
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = "clock packet exceeds size limit";
      continue;
    }
    std::string error;
    bytes.resize(static_cast<std::size_t>(received));
    const auto pong = decode_clock(bytes, max_bytes_, error);
    bytes.resize(max_bytes_ + 1);
    if (!pong) {
      ++decode_failures_;
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = error;
      continue;
    }
    if (peer.sin_addr.s_addr != target.sin_addr.s_addr || !pong->is_pong ||
      pong->session != session_ || pong->stream != "clock" || pong->sequence != sequence ||
      pong->t0_edge_send_mono_ns != t0)
    {
      ++unmatched_pongs_;
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_ = "unmatched clock pong";
      continue;
    }
    const auto t3 = now_ns();
    ++successes_;
    last_success_mono_ns_.store(t3);
    last_rtt_ns_.store(t3 - t0);
    {
      std::lock_guard<std::mutex> lock(error_mutex_);
      last_error_.clear();
    }
    if (callback_) callback_(*pong, t3);
    return true;
  }
}

}  // namespace dji_edge_transport
