#include "dji_edge_transport/dashboard_server.hpp"

#include <arpa/inet.h>
#include <cstring>
#include <fstream>
#include <nlohmann/json.hpp>
#include <sys/socket.h>
#include <unistd.h>

namespace dji_edge_transport {
namespace {
constexpr const char * kPage = R"(<!doctype html><meta charset=utf-8><title>DJI Transport Edge</title><style>body{font:16px system-ui;margin:3rem;max-width:56rem;color:#172033;background:#f7f8fa}h1{font-size:2rem}pre{background:#101827;color:#d9e3f0;padding:1rem;border-radius:12px;overflow:auto}input,button{padding:.65rem;border-radius:8px;border:1px solid #c8d0dc}button{background:#1b66d1;color:white}</style><h1>DJI Transport Edge</h1><p>Observer only. Video, UDP and ROS continue if this page is unavailable.</p><pre id=s>Loading…</pre><form id=f><label>Tablet IPv4 for clock <input id=ip placeholder=192.168.1.151></label> <button>Save for next launch</button></form><script>async function poll(){s.textContent=JSON.stringify(await (await fetch('/v1/state')).json(),null,2)}f.onsubmit=async e=>{e.preventDefault();await fetch('/v1/config',{method:'POST',body:JSON.stringify({android_clock_host:ip.value})});poll()};poll();setInterval(poll,1000)</script>)";
bool ipv4(const std::string & value) { in_addr address{}; return inet_pton(AF_INET, value.c_str(), &address) == 1; }
void reply(int client, int code, const std::string & type, const std::string & body)
{
  const std::string header = "HTTP/1.1 " + std::to_string(code) + "\r\nContent-Type: " + type +
    "\r\nContent-Length: " + std::to_string(body.size()) + "\r\nConnection: close\r\n\r\n";
  send(client, header.data(), header.size(), 0); send(client, body.data(), body.size(), 0);
}
}

DashboardServer::DashboardServer(std::uint16_t port, std::string local_config_path, StateProvider state_provider)
: port_(port), local_config_path_(std::move(local_config_path)), state_provider_(std::move(state_provider)) {}
DashboardServer::~DashboardServer() { stop(); }
bool DashboardServer::start()
{
  socket_ = socket(AF_INET, SOCK_STREAM, 0); if (socket_ < 0) return false;
  int reuse = 1; setsockopt(socket_, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
  sockaddr_in address{}; address.sin_family = AF_INET; address.sin_addr.s_addr = htonl(INADDR_LOOPBACK); address.sin_port = htons(port_);
  if (bind(socket_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0 || listen(socket_, 4) < 0) {
    std::lock_guard<std::mutex> lock(mutex_); error_ = std::strerror(errno); close(socket_); socket_ = -1; return false;
  }
  thread_ = std::thread(&DashboardServer::run, this); return true;
}
void DashboardServer::stop() { stopping_.store(true); if (socket_ >= 0) { shutdown(socket_, SHUT_RDWR); close(socket_); socket_ = -1; } if (thread_.joinable()) thread_.join(); }
std::string DashboardServer::error() const { std::lock_guard<std::mutex> lock(mutex_); return error_; }
void DashboardServer::run()
{
  while (!stopping_.load()) { const int client = accept(socket_, nullptr, nullptr); if (client < 0) continue; char buffer[2048]{}; const auto count = recv(client, buffer, sizeof(buffer) - 1, 0); const std::string request(buffer, count > 0 ? count : 0);
    if (request.rfind("GET / ", 0) == 0) reply(client, 200, "text/html; charset=utf-8", kPage);
    else if (request.rfind("GET /v1/state ", 0) == 0) reply(client, 200, "application/json", state_provider_());
    else if (request.rfind("POST /v1/config ", 0) == 0) { const auto body = request.substr(request.find("\r\n\r\n") + 4); try { const auto input = nlohmann::json::parse(body); const auto host = input.value("android_clock_host", ""); if (!host.empty() && !ipv4(host)) { reply(client, 400, "application/json", R"({"error":"android_clock_host must be IPv4"})"); } else { std::ofstream output(local_config_path_, std::ios::trunc); if (!output) reply(client, 500, "application/json", R"({"error":"cannot write local config"})"); else { output << "/dji_edge_transport:\n  ros__parameters:\n    android_clock_host: \"" << host << "\"\n"; reply(client, 200, "application/json", R"({"saved":true,"applies":"next launch"})"); } } } catch (...) { reply(client, 400, "application/json", R"({"error":"invalid JSON"})"); } }
    else {
      reply(client, 404, "application/json", R"({"error":"not found"})");
    }
    close(client);
  }
}
}  // namespace dji_edge_transport
