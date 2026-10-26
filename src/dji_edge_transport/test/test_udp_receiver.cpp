#include "dji_edge_transport/udp_receiver.hpp"

#include <arpa/inet.h>
#include <gtest/gtest.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <unistd.h>

#include <array>
#include <atomic>
#include <chrono>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

class SocketGuard {
public:
  explicit SocketGuard(int fd) : fd_(fd) {}
  ~SocketGuard() { if (fd_ >= 0) close(fd_); }
  int get() const { return fd_; }

private:
  int fd_;
};

std::uint16_t unused_port()
{
  SocketGuard socket_fd(socket(AF_INET, SOCK_DGRAM, 0));
  if (socket_fd.get() < 0) return 0;
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (bind(socket_fd.get(), reinterpret_cast<const sockaddr *>(&address), sizeof(address)) != 0) return 0;
  socklen_t size = sizeof(address);
  if (getsockname(socket_fd.get(), reinterpret_cast<sockaddr *>(&address), &size) != 0) return 0;
  return ntohs(address.sin_port);
}

bool send_packet(int fd, std::uint16_t port)
{
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_port = htons(port);
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  constexpr std::array<std::uint8_t, 3> packet{1, 2, 3};
  return sendto(fd, packet.data(), packet.size(), 0,
    reinterpret_cast<const sockaddr *>(&address), sizeof(address)) == static_cast<ssize_t>(packet.size());
}

bool wait_for(const std::atomic<unsigned> & count, unsigned expected)
{
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(1);
  while (count.load() < expected && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  return count.load() >= expected;
}

TEST(UdpReceiver, ReportsBindFailureAndAllowsRepeatedStop)
{
  SocketGuard occupied(socket(AF_INET, SOCK_DGRAM, 0));
  ASSERT_GE(occupied.get(), 0);
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = 0;
  ASSERT_EQ(bind(occupied.get(), reinterpret_cast<const sockaddr *>(&address), sizeof(address)), 0);
  socklen_t size = sizeof(address);
  ASSERT_EQ(getsockname(occupied.get(), reinterpret_cast<sockaddr *>(&address), &size), 0);

  dji_edge_transport::UdpReceiver receiver("127.0.0.1", ntohs(address.sin_port), 1200, {});
  EXPECT_FALSE(receiver.start());
  EXPECT_FALSE(receiver.ready());
  EXPECT_FALSE(receiver.last_error().empty());
  receiver.stop();
  receiver.stop();
}

TEST(UdpReceiver, StartsReceivesAndStopsIdempotently)
{
  const auto port = unused_port();
  ASSERT_NE(port, 0u);
  std::atomic<unsigned> received{0};
  dji_edge_transport::UdpReceiver receiver("127.0.0.1", port, 1200,
    [&](const std::vector<std::uint8_t> & packet, std::uint64_t, const dji_edge_transport::UdpPeer & peer) {
      if (packet.size() == 3 && peer.ipv4 == "127.0.0.1") ++received;
    });
  ASSERT_TRUE(receiver.start()) << receiver.last_error();
  ASSERT_TRUE(receiver.ready());
  EXPECT_TRUE(receiver.start());
  SocketGuard sender(socket(AF_INET, SOCK_DGRAM, 0));
  ASSERT_GE(sender.get(), 0);
  ASSERT_TRUE(send_packet(sender.get(), port));
  EXPECT_TRUE(wait_for(received, 1));
  receiver.stop();
  receiver.stop();
  EXPECT_FALSE(receiver.ready());
}

TEST(UdpReceiver, ContainsCallbackExceptionAndKeepsReceiving)
{
  const auto port = unused_port();
  ASSERT_NE(port, 0u);
  std::atomic<unsigned> calls{0};
  dji_edge_transport::UdpReceiver receiver("127.0.0.1", port, 1200,
    [&](const std::vector<std::uint8_t> &, std::uint64_t, const dji_edge_transport::UdpPeer &) {
      if (++calls == 1) throw std::runtime_error("expected callback fault");
    });
  ASSERT_TRUE(receiver.start()) << receiver.last_error();
  SocketGuard sender(socket(AF_INET, SOCK_DGRAM, 0));
  ASSERT_GE(sender.get(), 0);
  ASSERT_TRUE(send_packet(sender.get(), port));
  ASSERT_TRUE(wait_for(calls, 1));
  ASSERT_TRUE(send_packet(sender.get(), port));
  EXPECT_TRUE(wait_for(calls, 2));
  EXPECT_TRUE(receiver.ready());
  EXPECT_EQ(receiver.rejected(), 1u);
  receiver.stop();
}

TEST(UdpReceiver, ReportsSocketCreationFailure)
{
  const auto port = unused_port();
  ASSERT_NE(port, 0u);
  dji_edge_transport::UdpReceiver receiver("127.0.0.1", port, 1200, {});
  struct rlimit original{};
  ASSERT_EQ(getrlimit(RLIMIT_NOFILE, &original), 0);
  auto restricted = original;
  restricted.rlim_cur = 0;
  ASSERT_EQ(setrlimit(RLIMIT_NOFILE, &restricted), 0);
  const bool started = receiver.start();
  EXPECT_EQ(setrlimit(RLIMIT_NOFILE, &original), 0);
  EXPECT_FALSE(started);
  EXPECT_FALSE(receiver.ready());
  EXPECT_NE(receiver.last_error().find("socket"), std::string::npos);
  receiver.stop();
}

}  // namespace
