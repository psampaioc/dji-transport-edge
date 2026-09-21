#include "dji_edge_transport/session_evidence_writer.hpp"

#include <gtest/gtest.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <thread>

TEST(SessionEvidenceWriter, WritesBoundedSessionScopedRecords)
{
  const auto root = std::filesystem::temp_directory_path() / "dji-edge-evidence-test";
  std::filesystem::remove_all(root);
  {
    dji_edge_transport::SessionEvidenceWriter writer(root.string(), 2, 32, 1);
    writer.record("session-a", R"({"event":"one"})");
    writer.record("session-a", R"({"event":"two"})");
    writer.stop();
    EXPECT_EQ(writer.dropped(), 0u);
    EXPECT_EQ(writer.write_errors(), 0u);
  }
  const auto path = root / "session-a" / "edge-0.ndjson";
  std::ifstream input(path);
  std::string first;
  std::string second;
  ASSERT_TRUE(static_cast<bool>(std::getline(input, first)));
  ASSERT_TRUE(static_cast<bool>(std::getline(input, second)));
  EXPECT_NE(first.find("one"), std::string::npos);
  EXPECT_NE(second.find("two"), std::string::npos);
  std::filesystem::remove_all(root);
}
