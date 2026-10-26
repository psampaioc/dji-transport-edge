#include "dji_edge_transport/udp_receiver.hpp"

#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <exception>
#include <netinet/in.h>
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

UdpReceiver::UdpReceiver(std::string bind_host, std::uint16_t port, std::size_t max_bytes, Callback callback)
: bind_host_(std::move(bind_host)), port_(port), max_bytes_(max_bytes), callback_(std::move(callback)) {}

UdpReceiver::~UdpReceiver() { stop(); }

bool UdpReceiver::start()
{
  if (thread_.joinable()) return ready();
  stopping_.store(false);
  {
    std::lock_guard<std::mutex> lock(state_mutex_);
    start_complete_ = false;
    ready_ = false;
    last_error_.clear();
  }
  try {
    thread_ = std::thread(&UdpReceiver::run, this);
  } catch (const std::exception & exception) {
    std::lock_guard<std::mutex> lock(state_mutex_);
    start_complete_ = true;
    last_error_ = std::string("thread: ") + exception.what();
    return false;
  }
  std::unique_lock<std::mutex> lock(state_mutex_);
  ready_cv_.wait(lock, [this]() { return start_complete_; });
  return ready_;
}

void UdpReceiver::stop()
{
  stopping_.store(true);
  if (thread_.joinable()) thread_.join();
  std::lock_guard<std::mutex> lock(state_mutex_);
  ready_ = false;
}

bool UdpReceiver::ready() const
{
  std::lock_guard<std::mutex> lock(state_mutex_);
  return ready_;
}

std::string UdpReceiver::last_error() const
{
  std::lock_guard<std::mutex> lock(state_mutex_);
  return last_error_;
}

void UdpReceiver::run()
{
  const int socket_fd = ::socket(AF_INET, SOCK_DGRAM, 0);
  const auto fail_start = [this](const std::string & error) {
      {
        std::lock_guard<std::mutex> lock(state_mutex_);
        ready_ = false;
        start_complete_ = true;
        last_error_ = error;
      }
      ready_cv_.notify_all();
    };
  if (socket_fd < 0) {
    fail_start(std::string("socket: ") + std::strerror(errno));
    return;
  }
  int reuse = 1;
  if (setsockopt(socket_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse)) < 0) {
    const auto error = std::string("SO_REUSEADDR: ") + std::strerror(errno);
    close(socket_fd);
    fail_start(error);
    return;
  }
  timeval timeout{0, 200000};
  if (setsockopt(socket_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) < 0) {
    const auto error = std::string("SO_RCVTIMEO: ") + std::strerror(errno);
    close(socket_fd);
    fail_start(error);
    return;
  }
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(port_);
  address.sin_addr.s_addr = bind_host_ == "0.0.0.0" ? htonl(INADDR_ANY) : inet_addr(bind_host_.c_str());
  if (bind(socket_fd, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) {
    const auto error = std::string("bind: ") + std::strerror(errno);
    close(socket_fd);
    fail_start(error);
    return;
  }
  {
    std::lock_guard<std::mutex> lock(state_mutex_);
    ready_ = true;
    start_complete_ = true;
    last_error_.clear();
  }
  ready_cv_.notify_all();
  std::vector<std::uint8_t> buffer(max_bytes_ + 1);
  while (!stopping_.load()) {
    sockaddr_in peer_address{};
    socklen_t peer_size = sizeof(peer_address);
    const auto received = recvfrom(socket_fd, buffer.data(), buffer.size(), 0,
      reinterpret_cast<sockaddr *>(&peer_address), &peer_size);
    if (received < 0) {
      if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) continue;
      if (!stopping_.load()) {
        rejected_.fetch_add(1);
        std::lock_guard<std::mutex> lock(state_mutex_);
        ready_ = false;
        last_error_ = std::string("recvfrom: ") + std::strerror(errno);
      }
      break;
    }
    const auto receive_ns = now_ns();
    if (static_cast<std::size_t>(received) > max_bytes_) {
      rejected_.fetch_add(1);
      continue;
    }
    std::vector<std::uint8_t> packet(buffer.begin(), buffer.begin() + received);
    char peer_ipv4[INET_ADDRSTRLEN]{};
    inet_ntop(AF_INET, &peer_address.sin_addr, peer_ipv4, sizeof(peer_ipv4));
    const UdpPeer peer{peer_ipv4, ntohs(peer_address.sin_port)};
    received_.fetch_add(1);
    last_receive_mono_ns_.store(receive_ns);
    try {
      callback_(packet, receive_ns, peer);
    } catch (const std::exception & exception) {
      rejected_.fetch_add(1);
      std::lock_guard<std::mutex> lock(state_mutex_);
      last_error_ = std::string("callback: ") + exception.what();
    } catch (...) {
      rejected_.fetch_add(1);
      std::lock_guard<std::mutex> lock(state_mutex_);
      last_error_ = "callback: unknown exception";
    }
  }
  if (close(socket_fd) < 0) {
    std::lock_guard<std::mutex> lock(state_mutex_);
    ready_ = false;
    last_error_ = std::string("close: ") + std::strerror(errno);
  }
}

}  // namespace dji_edge_transport
