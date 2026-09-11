#include "dji_edge_transport/primary_video_pipeline.hpp"

#include <gst/app/gstappsink.h>
#include <gst/gst.h>

#include <chrono>
#include <cstring>
#include <functional>
#include <sstream>

namespace dji_edge_transport {
namespace {
std::uint64_t now_ns()
{
  return static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count());
}

GstFlowReturn on_sample(GstAppSink * sink, gpointer user_data)
{
  auto * self = static_cast<PrimaryVideoPipeline *>(user_data);
  GstSample * sample = gst_app_sink_pull_sample(sink);
  if (!sample) return GST_FLOW_OK;
  GstCaps * caps = gst_sample_get_caps(sample);
  GstStructure * structure = caps ? gst_caps_get_structure(caps, 0) : nullptr;
  gint width = 0;
  gint height = 0;
  if (!structure || !gst_structure_get_int(structure, "width", &width) ||
    !gst_structure_get_int(structure, "height", &height))
  {
    gst_sample_unref(sample);
    return GST_FLOW_OK;
  }
  GstBuffer * buffer = gst_sample_get_buffer(sample);
  GstMapInfo map{};
  if (!buffer || !gst_buffer_map(buffer, &map, GST_MAP_READ)) {
    gst_sample_unref(sample);
    return GST_FLOW_OK;
  }
  DecodedFrame frame;
  frame.width = static_cast<std::uint32_t>(width);
  frame.height = static_cast<std::uint32_t>(height);
  frame.decoded_mono_ns = now_ns();
  frame.bgr.assign(map.data, map.data + map.size);
  gst_buffer_unmap(buffer, &map);
  gst_sample_unref(sample);
  self->accept_frame(std::move(frame));
  return GST_FLOW_OK;
}

GstPadProbeReturn on_source_buffer(GstPad *, GstPadProbeInfo * info, gpointer user_data)
{
  auto * self = static_cast<PrimaryVideoPipeline *>(user_data);
  if (GST_PAD_PROBE_INFO_BUFFER(info)) self->observe_rtp();
  return GST_PAD_PROBE_OK;
}
}

PrimaryVideoPipeline::PrimaryVideoPipeline(std::string bind_host, std::uint16_t port, int payload_type,
  int latency_ms, FrameHandoff & handoff, std::function<void(const std::string &)> logger)
: bind_host_(std::move(bind_host)), port_(port), payload_type_(payload_type), latency_ms_(latency_ms),
  handoff_(handoff), logger_(std::move(logger))
{
  gst_init(nullptr, nullptr);
}

PrimaryVideoPipeline::~PrimaryVideoPipeline() { close(); }

std::string PrimaryVideoPipeline::description(const std::string & decoder) const
{
  std::ostringstream graph;
  graph << "udpsrc name=primary_source address=" << bind_host_ << " port=" << port_
        << " caps=application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload=" << payload_type_
        << " ! rtpjitterbuffer latency=" << latency_ms_ << " drop-on-latency=true do-lost=true"
        << " ! rtph264depay wait-for-keyframe=true request-keyframe=true ! h264parse ! " << decoder
        << " ! videoconvert ! video/x-raw,format=BGR ! appsink name=primary_sink emit-signals=false"
        << " max-buffers=1 drop=true sync=false";
  return graph.str();
}

bool PrimaryVideoPipeline::start_with_decoder(const std::string & decoder, const std::string & backend, const std::string & reason)
{
  GError * error = nullptr;
  pipeline_ = gst_parse_launch(description(decoder).c_str(), &error);
  if (!pipeline_) {
    set_error(error ? error->message : "GStreamer graph creation failed");
    if (error) g_error_free(error);
    return false;
  }
  sink_ = gst_bin_get_by_name(GST_BIN(pipeline_), "primary_sink");
  auto * source = gst_bin_get_by_name(GST_BIN(pipeline_), "primary_source");
  if (source) {
    auto * pad = gst_element_get_static_pad(source, "src");
    if (pad) {
      gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, on_source_buffer, this, nullptr);
      gst_object_unref(pad);
    }
    gst_object_unref(source);
  }
  bus_ = gst_element_get_bus(pipeline_);
  GstAppSinkCallbacks callbacks{};
  callbacks.new_sample = on_sample;
  gst_app_sink_set_callbacks(GST_APP_SINK(sink_), &callbacks, this, nullptr);
  const auto result = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
  if (result == GST_STATE_CHANGE_FAILURE) {
    set_error("GStreamer pipeline could not enter PLAYING");
    close();
    return false;
  }
  decoder_backend_ = backend;
  decoder_reason_ = reason;
  pipeline_error_.clear();
  return true;
}

void PrimaryVideoPipeline::accept_frame(DecodedFrame frame)
{
  std::lock_guard<std::mutex> lock(mutex_);
  width_ = frame.width;
  height_ = frame.height;
  last_decoded_mono_ns_ = frame.decoded_mono_ns;
  ++decoded_count_;
  handoff_.replace(std::move(frame));
}

void PrimaryVideoPipeline::observe_rtp()
{
  std::lock_guard<std::mutex> lock(mutex_);
  last_rtp_mono_ns_ = static_cast<std::uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count());
}

bool PrimaryVideoPipeline::start()
{
  if (closed_.load()) return false;
  auto * factory = gst_element_factory_find("nvh264dec");
  if (factory) {
    gst_object_unref(factory);
    if (start_with_decoder("nvh264dec", "nvidia", "NVIDIA decoder pipeline started")) return true;
  }
  return start_with_decoder("avdec_h264", "cpu", "NVIDIA decoder unavailable; CPU fallback active");
}

void PrimaryVideoPipeline::set_error(const std::string & error)
{
  std::lock_guard<std::mutex> lock(mutex_);
  pipeline_error_ = error;
  if (logger_) logger_(error);
}

void PrimaryVideoPipeline::poll_bus()
{
  if (!bus_ || closed_.load()) return;
  while (auto * message = gst_bus_pop_filtered(bus_, static_cast<GstMessageType>(GST_MESSAGE_ERROR | GST_MESSAGE_EOS))) {
    if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR) {
      GError * error = nullptr;
      gchar * debug = nullptr;
      gst_message_parse_error(message, &error, &debug);
      set_error(error ? error->message : "GStreamer error");
      if (error) g_error_free(error);
      if (debug) g_free(debug);
    } else {
      set_error("GStreamer EOS");
    }
    gst_message_unref(message);
  }
}

void PrimaryVideoPipeline::close()
{
  if (closed_.exchange(true)) return;
  if (pipeline_) gst_element_set_state(pipeline_, GST_STATE_NULL);
  if (bus_) gst_object_unref(bus_);
  if (sink_) gst_object_unref(sink_);
  if (pipeline_) gst_object_unref(pipeline_);
  bus_ = nullptr;
  sink_ = nullptr;
  pipeline_ = nullptr;
}

VideoSnapshot PrimaryVideoPipeline::snapshot() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  VideoSnapshot snapshot;
  snapshot.last_rtp_mono_ns = last_rtp_mono_ns_;
  snapshot.last_decoded_mono_ns = last_decoded_mono_ns_;
  snapshot.decoded_count = decoded_count_;
  snapshot.width = width_;
  snapshot.height = height_;
  snapshot.decoder_backend = decoder_backend_;
  snapshot.decoder_reason = decoder_reason_;
  snapshot.pipeline_error = pipeline_error_;
  return snapshot;
}

}  // namespace dji_edge_transport
