#include "dji_edge_transport/session_evidence_writer.hpp"

#include <filesystem>
#include <fstream>

namespace dji_edge_transport {

SessionEvidenceWriter::SessionEvidenceWriter(std::string root, std::size_t queue_capacity,
  std::size_t max_file_bytes, std::size_t max_files)
: root_(std::move(root)), queue_capacity_(queue_capacity), max_file_bytes_(max_file_bytes),
  max_files_(max_files), thread_(&SessionEvidenceWriter::run, this) {}

SessionEvidenceWriter::~SessionEvidenceWriter() { stop(); }

void SessionEvidenceWriter::record(std::string session, std::string json_line)
{
  if (session.empty() || json_line.empty()) return;
  std::lock_guard<std::mutex> lock(mutex_);
  if (stopping_ || queue_.size() >= queue_capacity_) {
    ++dropped_;
    return;
  }
  queue_.push_back({std::move(session), std::move(json_line)});
  cv_.notify_one();
}

void SessionEvidenceWriter::stop()
{
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (stopping_) return;
    stopping_ = true;
  }
  cv_.notify_one();
  if (thread_.joinable()) thread_.join();
}

void SessionEvidenceWriter::run()
{
  std::unique_lock<std::mutex> lock(mutex_);
  while (true) {
    cv_.wait(lock, [this]() { return stopping_ || !queue_.empty(); });
    if (queue_.empty() && stopping_) return;
    Item item = std::move(queue_.front());
    queue_.pop_front();
    lock.unlock();
    if (!write(item)) ++write_errors_;
    lock.lock();
  }
}

bool SessionEvidenceWriter::write(const Item & item)
{
  try {
    if (item.session != active_session_) {
      active_session_ = item.session;
      active_file_index_ = 0;
      active_file_bytes_ = 0;
    }
    const auto directory = std::filesystem::path(root_) / active_session_;
    std::filesystem::create_directories(directory);
    const auto bytes = item.json_line.size() + 1;
    if (active_file_bytes_ + bytes > max_file_bytes_) {
      ++active_file_index_;
      active_file_bytes_ = 0;
    }
    if (active_file_index_ >= max_files_) return false;
    const auto path = directory / ("edge-" + std::to_string(active_file_index_) + ".ndjson");
    std::ofstream output(path, std::ios::app);
    if (!output) return false;
    output << item.json_line << '\n';
    if (!output) return false;
    active_file_bytes_ += bytes;
    return true;
  } catch (...) {
    return false;
  }
}

}  // namespace dji_edge_transport
