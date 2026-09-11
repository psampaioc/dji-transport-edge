#include "dji_edge_transport/udp_receiver.hpp"

#include <arpa/inet.h>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
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

void UdpReceiver::start()
{
  if (thread_.joinable()) return;
  stopping_.store(false);
  thread_ = std::thread(&UdpReceiver::run, this);
}

void UdpReceiver::stop()
{
  stopping_.store(true);
  if (socket_ >= 0) {
    shutdown(socket_, SHUT_RDWR);
    close(socket_);
    socket_ = -1;
  }
  if (thread_.joinable()) thread_.join();
}

void UdpReceiver::run()
{
  socket_ = ::socket(AF_INET, SOCK_DGRAM, 0);
  if (socket_ < 0) return;
  int reuse = 1;
  setsockopt(socket_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
  timeval timeout{0, 200000};
  setsockopt(socket_, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(port_);
  address.sin_addr.s_addr = bind_host_ == "0.0.0.0" ? htonl(INADDR_ANY) : inet_addr(bind_host_.c_str());
  if (bind(socket_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) {
    close(socket_);
    socket_ = -1;
    return;
  }
  std::vector<std::uint8_t> buffer(max_bytes_ + 1);
  while (!stopping_.load()) {
    const auto received = recvfrom(socket_, buffer.data(), buffer.size(), 0, nullptr, nullptr);
    if (received < 0) {
      if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) continue;
      if (!stopping_.load()) rejected_.fetch_add(1);
      break;
    }
    const auto receive_ns = now_ns();
    if (static_cast<std::size_t>(received) > max_bytes_) {
      rejected_.fetch_add(1);
      continue;
    }
    std::vector<std::uint8_t> packet(buffer.begin(), buffer.begin() + received);
    received_.fetch_add(1);
    last_receive_mono_ns_.store(receive_ns);
    callback_(packet, receive_ns);
  }
}

}  // namespace dji_edge_transport
