#pragma once

#include <atomic>
#include <condition_variable>
#include <cstddef>
#include <deque>
#include <mutex>
#include <string>
#include <thread>

namespace dji_edge_transport {

class SessionEvidenceWriter {
public:
  SessionEvidenceWriter(std::string root, std::size_t queue_capacity = 64,
    std::size_t max_file_bytes = 1 << 20, std::size_t max_files = 3);
  ~SessionEvidenceWriter();
  SessionEvidenceWriter(const SessionEvidenceWriter &) = delete;
  SessionEvidenceWriter & operator=(const SessionEvidenceWriter &) = delete;
  void record(std::string session, std::string json_line);
  void stop();
  std::uint64_t dropped() const { return dropped_.load(); }
  std::uint64_t write_errors() const { return write_errors_.load(); }

private:
  struct Item { std::string session; std::string json_line; };
  void run();
  bool write(const Item & item);

  std::string root_;
  std::size_t queue_capacity_;
  std::size_t max_file_bytes_;
  std::size_t max_files_;
  std::mutex mutex_;
  std::condition_variable cv_;
  std::deque<Item> queue_;
  bool stopping_{false};
  std::thread thread_;
  std::string active_session_;
  std::size_t active_file_index_{0};
  std::size_t active_file_bytes_{0};
  std::atomic<std::uint64_t> dropped_{0};
  std::atomic<std::uint64_t> write_errors_{0};
};

}  // namespace dji_edge_transport
