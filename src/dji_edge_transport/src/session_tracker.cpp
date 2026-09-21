#include "dji_edge_transport/session_tracker.hpp"

namespace dji_edge_transport {

SessionTracker::SessionTracker(std::size_t retired_capacity) : retired_capacity_(retired_capacity) {}

void SessionTracker::retire_active()
{
  if (active_session_.empty() || retired_capacity_ == 0) return;
  retired_order_.push_back(active_session_);
  retired_sessions_.insert(active_session_);
  while (retired_order_.size() > retired_capacity_) {
    retired_sessions_.erase(retired_order_.front());
    retired_order_.pop_front();
  }
}

SessionObservation SessionTracker::observe(const std::string & session, const std::string & logical_stream,
  std::uint64_t sequence)
{
  if (session.empty() || logical_stream.empty() || sequence == 0) {
    return SessionObservation::rejected_sequence;
  }
  if (retired_sessions_.count(session) != 0) return SessionObservation::rejected_retired;
  bool transitioned = false;
  if (active_session_ != session) {
    retire_active();
    active_session_ = session;
    last_sequence_.clear();
    transitioned = true;
  }
  auto & previous = last_sequence_[logical_stream];
  if (previous != 0 && sequence <= previous) return SessionObservation::rejected_sequence;
  previous = sequence;
  return transitioned ? SessionObservation::transitioned : SessionObservation::accepted;
}

}  // namespace dji_edge_transport
