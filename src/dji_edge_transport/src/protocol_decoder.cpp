#include "dji_edge_transport/protocol_decoder.hpp"

#include <nlohmann/json.hpp>

#include <cmath>
#include <limits>

namespace dji_edge_transport {
namespace {
using Json = nlohmann::json;

bool valid_root(const Json & root, std::string & error, std::size_t max_bytes, std::size_t size)
{
  if (size == 0 || size > max_bytes) {
    error = "invalid datagram size";
    return false;
  }
  if (!root.is_object() || root.value("v", 0) != 1) {
    error = "unsupported protocol envelope";
    return false;
  }
  return true;
}

std::string string_value(const Json & root, const char * key)
{
  return root.contains(key) && root[key].is_string() ? root[key].get<std::string>() : std::string{};
}

std::uint64_t uint_value(const Json & root, const char * key)
{
  return root.contains(key) && root[key].is_number_unsigned() ? root[key].get<std::uint64_t>() : 0;
}

bool exact_stream(const Json & root, const std::string & expected, std::string & error)
{
  if (string_value(root, "stream") != expected) {
    error = "invalid packet stream";
    return false;
  }
  return true;
}

std::optional<double> compact_field_number(const Json & fields, const char * key)
{
  if (!fields.is_object() || !fields.contains(key)) return std::nullopt;
  const auto & field = fields.at(key);
  if (!field.is_object() || field.value("valid", true) != true ||
    !field.contains("value") || !field.at("value").is_number())
  {
    return std::nullopt;
  }
  const double value = field.at("value").get<double>();
  return std::isfinite(value) ? std::optional<double>(value) : std::nullopt;
}

bool compact_field_bool(const Json & fields, const char * key)
{
  if (!fields.is_object() || !fields.contains(key)) return false;
  const auto & field = fields.at(key);
  return field.is_object() && field.value("valid", true) == true &&
    field.contains("value") && field.at("value").is_boolean() &&
    field.at("value").get<bool>();
}

const Json & body_or_root(const Json & root)
{
  return root.contains("data") && root["data"].is_object() ? root["data"] : root;
}

}  // namespace

std::optional<TelemetryPacket> decode_telemetry(
  const std::vector<std::uint8_t> & bytes, std::size_t max_bytes, std::string & error)
{
  try {
    const auto root = Json::parse(bytes.begin(), bytes.end());
    if (!valid_root(root, error, max_bytes, bytes.size())) {
      return std::nullopt;
    }
    const std::string type = string_value(root, "type");
    if (type != "flight" && type != "rtk" && type != "gimbal") {
      error = "not telemetry";
      return std::nullopt;
    }
    if (!exact_stream(root, type + ":0", error)) return std::nullopt;
    const auto & body = body_or_root(root);
    TelemetryPacket packet;
    packet.type = type;
    packet.session = string_value(root, root.contains("session") ? "session" : "session_id");
    packet.sequence = uint_value(root, "seq");
    packet.android_mono_ns = uint_value(root, "rx_mono_ns");
    if (packet.android_mono_ns == 0) {
      packet.android_mono_ns = uint_value(root, "android_mono_ns");
    }
    if (packet.session.empty() || packet.sequence == 0 || packet.android_mono_ns == 0) {
      error = "missing telemetry envelope fields";
      return std::nullopt;
    }
    if (body.contains("fields") && body.at("fields").is_object()) {
      const auto & fields = body.at("fields");
      const auto latitude = compact_field_number(fields, "aircraft.latitude_deg");
      const auto longitude = compact_field_number(fields, "aircraft.longitude_deg");
      const auto altitude = compact_field_number(fields, "aircraft.altitude_m");
      const auto heading = compact_field_number(fields, "heading_deg");
      const auto rtk_latitude = compact_field_number(fields, "fusion.latitude_deg");
      const auto rtk_longitude = compact_field_number(fields, "fusion.longitude_deg");
      const auto pitch = compact_field_number(fields, "attitude.pitch_deg");
      packet.latitude_deg = latitude.value_or(0.0);
      packet.longitude_deg = longitude.value_or(0.0);
      packet.altitude_m = altitude.value_or(0.0);
      packet.heading_deg = heading.value_or(0.0);
      packet.position_valid = latitude.has_value() && longitude.has_value() &&
        altitude.has_value() && heading.has_value();
      packet.rtk_valid = compact_field_bool(fields, "is_being_used") &&
        rtk_latitude.has_value() && rtk_longitude.has_value();
      if (packet.rtk_valid) {
        packet.latitude_deg = *rtk_latitude;
        packet.longitude_deg = *rtk_longitude;
      }
      packet.gimbal_pitch_valid = pitch.has_value();
      packet.gimbal_pitch_deg = pitch.value_or(0.0);
      packet.gimbal_android_mono_ns = packet.gimbal_pitch_valid ? packet.android_mono_ns : 0;
    } else {
      error = "missing compact telemetry fields";
      return std::nullopt;
    }
    return packet;
  } catch (const std::exception & exception) {
    error = exception.what();
    return std::nullopt;
  }
}

std::optional<HealthPacket> decode_health(
  const std::vector<std::uint8_t> & bytes, std::size_t max_bytes, std::string & error)
{
  try {
    const auto root = Json::parse(bytes.begin(), bytes.end());
    if (!valid_root(root, error, max_bytes, bytes.size())) return std::nullopt;
    if (string_value(root, "type") != "health") {
      error = "not health";
      return std::nullopt;
    }
    if (!exact_stream(root, "health:0", error)) return std::nullopt;
    const auto & data = body_or_root(root);
    if (!data.is_object()) {
      error = "invalid health data";
      return std::nullopt;
    }
    const auto required_counter = [&data, &error](const char * key) -> std::optional<std::uint64_t> {
      if (!data.contains(key) || !data.at(key).is_number_unsigned()) {
        error = std::string("invalid health counter: ") + key;
        return std::nullopt;
      }
      return data.at(key).get<std::uint64_t>();
    };
    HealthPacket packet;
    packet.session = string_value(root, "session");
    packet.sequence = uint_value(root, "seq");
    packet.android_mono_ns = uint_value(root, "rx_mono_ns");
    if (packet.session.empty() || packet.sequence == 0 || packet.android_mono_ns == 0) {
      error = "missing health envelope fields";
      return std::nullopt;
    }
    const auto callbacks = required_counter("primary_video_callbacks");
    const auto callback_drops = required_counter("primary_video_callback_drops");
    const auto rtp_packets = required_counter("primary_rtp_packets");
    const auto access_units = required_counter("primary_access_units");
    const auto telemetry_drops = required_counter("telemetry_queue_drops");
    const auto telemetry_errors = required_counter("telemetry_socket_errors");
    const auto video_errors = required_counter("video_socket_errors");
    if (!callbacks || !callback_drops || !rtp_packets || !access_units || !telemetry_drops ||
      !telemetry_errors || !video_errors)
    {
      return std::nullopt;
    }
    packet.primary_video_callbacks = *callbacks;
    packet.primary_video_callback_drops = *callback_drops;
    packet.primary_rtp_packets = *rtp_packets;
    packet.primary_access_units = *access_units;
    packet.telemetry_queue_drops = *telemetry_drops;
    packet.telemetry_socket_errors = *telemetry_errors;
    packet.video_socket_errors = *video_errors;
    const auto optional_counter = [&data](const char * key) -> std::optional<std::uint64_t> {
      if (!data.contains(key) || !data.at(key).is_number_unsigned()) return std::nullopt;
      return data.at(key).get<std::uint64_t>();
    };
    packet.primary_rtp_bytes = optional_counter("primary_rtp_bytes");
    packet.telemetry_datagrams = optional_counter("telemetry_datagrams");
    packet.telemetry_bytes = optional_counter("telemetry_bytes");
    packet.metadata_datagrams = optional_counter("metadata_datagrams");
    packet.metadata_bytes = optional_counter("metadata_bytes");
    packet.clock_pongs_sent = optional_counter("clock_pongs_sent");
    return packet;
  } catch (const std::exception & exception) {
    error = exception.what();
    return std::nullopt;
  }
}

std::optional<FrameMetadata> decode_frame_metadata(
  const std::vector<std::uint8_t> & bytes, std::size_t max_bytes, std::string & error)
{
  try {
    const auto root = Json::parse(bytes.begin(), bytes.end());
    if (!valid_root(root, error, max_bytes, bytes.size())) {
      return std::nullopt;
    }
    const std::string type = string_value(root, "type");
    if (type != "video_au" && type != "frame_meta") {
      error = "not frame metadata";
      return std::nullopt;
    }
    FrameMetadata metadata;
    metadata.session = string_value(root, root.contains("session") ? "session" : "session_id");
    metadata.feed = string_value(root, root.contains("feed") ? "feed" : "stream");
    metadata.frame_seq = uint_value(root, "frame_seq");
    metadata.rtp_ssrc = static_cast<std::uint32_t>(uint_value(root, "rtp_ssrc"));
    metadata.rtp_ts = static_cast<std::uint32_t>(uint_value(root, "rtp_ts"));
    metadata.first_byte_mono_ns = uint_value(root, "au_first_byte_rx_mono_ns");
    metadata.complete_mono_ns = uint_value(root, "au_complete_rx_mono_ns");
    if (metadata.first_byte_mono_ns == 0) {
      metadata.first_byte_mono_ns = uint_value(root, "android_mono_ns");
    }
    if (metadata.complete_mono_ns == 0) {
      metadata.complete_mono_ns = metadata.first_byte_mono_ns;
    }
    if (metadata.session.empty() || metadata.feed != "primary" || metadata.frame_seq == 0 ||
      metadata.rtp_ssrc == 0 || metadata.first_byte_mono_ns == 0)
    {
      error = "invalid Primary frame metadata";
      return std::nullopt;
    }
    if (root.contains("dji_source_timestamp_ns")) {
      const auto value = uint_value(root, "dji_source_timestamp_ns");
      if (value == 0 || !root.contains("dji_timestamp_source") || !root["dji_timestamp_source"].is_string()) {
        error = "invalid DJI source timestamp";
        return std::nullopt;
      }
      metadata.dji_source_timestamp_ns = value;
      metadata.dji_timestamp_source = root["dji_timestamp_source"].get<std::string>();
    }
    return metadata;
  } catch (const std::exception & exception) {
    error = exception.what();
    return std::nullopt;
  }
}

std::optional<ClockPacket> decode_clock(
  const std::vector<std::uint8_t> & bytes, std::size_t max_bytes, std::string & error)
{
  try {
    const auto root = Json::parse(bytes.begin(), bytes.end());
    if (!valid_root(root, error, max_bytes, bytes.size())) return std::nullopt;
    const auto type = string_value(root, "type");
    if (type != "clock_ping" && type != "clock_pong") {
      error = "not a clock packet";
      return std::nullopt;
    }
    ClockPacket packet;
    packet.is_pong = type == "clock_pong";
    packet.session = string_value(root, "session");
    packet.stream = string_value(root, "stream");
    packet.sequence = uint_value(root, "seq");
    packet.t0_edge_send_mono_ns = uint_value(root, "t0_edge_send_mono_ns");
    packet.t1_android_rx_mono_ns = uint_value(root, "t1_android_rx_mono_ns");
    packet.t2_android_tx_mono_ns = uint_value(root, "t2_android_tx_mono_ns");
    if (packet.t0_edge_send_mono_ns == 0 ||
      (packet.is_pong && (packet.t1_android_rx_mono_ns == 0 || packet.t2_android_tx_mono_ns < packet.t1_android_rx_mono_ns)))
    {
      error = "invalid clock timestamps";
      return std::nullopt;
    }
    return packet;
  } catch (const std::exception & exception) {
    error = exception.what();
    return std::nullopt;
  }
}

}  // namespace dji_edge_transport
