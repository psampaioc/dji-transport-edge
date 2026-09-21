#include "dji_edge_transport/clock_pinger.hpp"

#include <arpa/inet.h>
#include <gtest/gtest.h>
#include <nlohmann/json.hpp>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <array>
#include <chrono>
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

TEST(ClockPinger, AcceptsPongFromEphemeralSourcePort)
{
  SocketGuard server(socket(AF_INET, SOCK_DGRAM, 0));
  ASSERT_GE(server.get(), 0);
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = 0;
  ASSERT_EQ(bind(server.get(), reinterpret_cast<const sockaddr *>(&address), sizeof(address)), 0);
  socklen_t address_size = sizeof(address);
  ASSERT_EQ(getsockname(server.get(), reinterpret_cast<sockaddr *>(&address), &address_size), 0);

  std::atomic<bool> callback_called{false};
  std::thread responder([&]() {
    std::array<char, 1200> buffer{};
    sockaddr_in client{};
    socklen_t client_size = sizeof(client);
    const auto received = recvfrom(server.get(), buffer.data(), buffer.size(), 0,
      reinterpret_cast<sockaddr *>(&client), &client_size);
    ASSERT_GT(received, 0);
    const auto ping = nlohmann::json::parse(buffer.data(), buffer.data() + received);
    const auto pong = nlohmann::json{
      {"v", 1}, {"type", "clock_pong"}, {"session", ping.at("session")},
      {"stream", "clock"}, {"seq", ping.at("seq")},
      {"t0_edge_send_mono_ns", ping.at("t0_edge_send_mono_ns")},
      {"t1_android_rx_mono_ns", 200ULL}, {"t2_android_tx_mono_ns", 210ULL}}.dump();
    ASSERT_EQ(sendto(server.get(), pong.data(), pong.size(), 0,
      reinterpret_cast<const sockaddr *>(&client), client_size),
      static_cast<ssize_t>(pong.size()));
  });

  dji_edge_transport::ClockPinger pinger("127.0.0.1", ntohs(address.sin_port), "edge-test",
    1.0, 0.5, 1200,
    [&](const dji_edge_transport::ClockPacket & packet, std::uint64_t) {
      callback_called.store(packet.is_pong && packet.t1_android_rx_mono_ns == 200);
    });
  EXPECT_TRUE(pinger.exchange_once());
  responder.join();
  EXPECT_TRUE(callback_called.load());
  EXPECT_EQ(pinger.attempts(), 1u);
  EXPECT_EQ(pinger.successes(), 1u);
  EXPECT_EQ(pinger.timeouts(), 0u);
  EXPECT_GT(pinger.last_success_mono_ns(), 0u);
  EXPECT_GT(pinger.last_rtt_ns(), 0u);
  EXPECT_EQ(pinger.send_failures(), 0u);
  EXPECT_EQ(pinger.decode_failures(), 0u);
  EXPECT_EQ(pinger.unmatched_pongs(), 0u);
}

TEST(ClockPinger, SeparatesInvalidDestinationFromTimeout)
{
  dji_edge_transport::ClockPinger invalid("not-an-ipv4", 5502, "edge-test", 1.0, 0.01, 1200, {});
  EXPECT_FALSE(invalid.exchange_once());
  EXPECT_EQ(invalid.attempts(), 1u);
  EXPECT_EQ(invalid.send_failures(), 1u);
  EXPECT_EQ(invalid.timeouts(), 0u);
  EXPECT_EQ(invalid.decode_failures(), 0u);
  EXPECT_EQ(invalid.unmatched_pongs(), 0u);

  SocketGuard server(socket(AF_INET, SOCK_DGRAM, 0));
  ASSERT_GE(server.get(), 0);
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = 0;
  ASSERT_EQ(bind(server.get(), reinterpret_cast<const sockaddr *>(&address), sizeof(address)), 0);
  socklen_t address_size = sizeof(address);
  ASSERT_EQ(getsockname(server.get(), reinterpret_cast<sockaddr *>(&address), &address_size), 0);

  dji_edge_transport::ClockPinger timeout("127.0.0.1", ntohs(address.sin_port), "edge-test",
    1.0, 0.01, 1200, {});
  EXPECT_FALSE(timeout.exchange_once());
  EXPECT_EQ(timeout.attempts(), 1u);
  EXPECT_EQ(timeout.send_failures(), 0u);
  EXPECT_EQ(timeout.timeouts(), 1u);
  EXPECT_EQ(timeout.decode_failures(), 0u);
  EXPECT_EQ(timeout.unmatched_pongs(), 0u);
}

TEST(ClockPinger, BackgroundProbesReuseOneSourcePort)
{
  SocketGuard server(socket(AF_INET, SOCK_DGRAM, 0));
  ASSERT_GE(server.get(), 0);
  sockaddr_in address{};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = 0;
  ASSERT_EQ(bind(server.get(), reinterpret_cast<const sockaddr *>(&address), sizeof(address)), 0);
  socklen_t address_size = sizeof(address);
  ASSERT_EQ(getsockname(server.get(), reinterpret_cast<sockaddr *>(&address), &address_size), 0);

  std::atomic<int> replies{0};
  std::atomic<std::uint16_t> first_port{0};
  std::atomic<std::uint16_t> second_port{0};
  std::thread responder([&]() {
    for (int index = 0; index < 2; ++index) {
      std::array<char, 1200> buffer{};
      sockaddr_in client{};
      socklen_t client_size = sizeof(client);
      const auto received = recvfrom(server.get(), buffer.data(), buffer.size(), 0,
        reinterpret_cast<sockaddr *>(&client), &client_size);
      if (received <= 0) return;
      if (index == 0) first_port.store(ntohs(client.sin_port));
      else second_port.store(ntohs(client.sin_port));
      const auto ping = nlohmann::json::parse(buffer.data(), buffer.data() + received);
      const auto pong = nlohmann::json{{"v", 1}, {"type", "clock_pong"},
        {"session", ping.at("session")}, {"stream", "clock"}, {"seq", ping.at("seq")},
        {"t0_edge_send_mono_ns", ping.at("t0_edge_send_mono_ns")},
        {"t1_android_rx_mono_ns", 200ULL}, {"t2_android_tx_mono_ns", 210ULL}}.dump();
      if (sendto(server.get(), pong.data(), pong.size(), 0,
          reinterpret_cast<const sockaddr *>(&client), client_size) == static_cast<ssize_t>(pong.size())) {
        ++replies;
      }
    }
  });

  dji_edge_transport::ClockPinger pinger("127.0.0.1", ntohs(address.sin_port), "edge-test",
    0.01, 0.1, 1200, {});
  pinger.start();
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(1);
  while (replies.load() < 2 && std::chrono::steady_clock::now() < deadline) {
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  pinger.stop();
  responder.join();
  EXPECT_EQ(replies.load(), 2);
  EXPECT_NE(first_port.load(), 0);
  EXPECT_EQ(second_port.load(), first_port.load());
  EXPECT_GE(pinger.successes(), 2u);
}

}  // namespace
