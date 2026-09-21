#pragma once

#include <cstddef>
#include <cstdint>
#include <deque>
#include <map>
#include <string>
#include <unordered_set>

namespace dji_edge_transport {

enum class SessionObservation { accepted, transitioned, rejected_retired, rejected_sequence };

class SessionTracker {
public:
  explicit SessionTracker(std::size_t retired_capacity = 4);
  SessionObservation observe(const std::string & session, const std::string & logical_stream,
    std::uint64_t sequence);
  const std::string & active_session() const { return active_session_; }

private:
  void retire_active();

  std::size_t retired_capacity_;
  std::string active_session_;
  std::deque<std::string> retired_order_;
  std::unordered_set<std::string> retired_sessions_;
  std::map<std::string, std::uint64_t> last_sequence_;
};

}  // namespace dji_edge_transport
