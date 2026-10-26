#include "dji_edge_transport/dashboard_server.hpp"

#include <arpa/inet.h>
#include <algorithm>
#include <cstring>
#include <fstream>
#include <nlohmann/json.hpp>
#include <sstream>
#include <sys/socket.h>
#include <unistd.h>

namespace dji_edge_transport {
namespace {
constexpr const char * kPage = R"(<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DJI Transport Edge</title>
<style>
:root{--ink:#16243a;--muted:#69778c;--line:#dce4ee;--panel:#fff;--canvas:#f3f6f9;--blue:#1f67cf;--good:#157a55;--warn:#a95d00;--bad:#b23a3a;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font:15px/1.45 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{width:min(100% - 2.5rem,118rem);margin:0 auto;padding:2rem 0 3rem}header{display:flex;align-items:start;justify-content:space-between;gap:2rem;margin-bottom:1.5rem}h1{font-size:clamp(1.65rem,3vw,2.35rem);letter-spacing:-.04em;margin:0}header p{margin:.35rem 0 0;color:var(--muted)}.state{font:600 .85rem var(--mono);padding:.42rem .7rem;border-radius:999px;background:#e8f3ed;color:var(--good);white-space:nowrap}.state.waiting{background:#fff2de;color:var(--warn)}.state.error{background:#ffe8e8;color:var(--bad)}.tabs{display:flex;gap:.45rem;border-bottom:1px solid var(--line);margin-bottom:1.25rem;overflow-x:auto}.tab{appearance:none;background:transparent;border:0;border-bottom:3px solid transparent;color:var(--muted);font:600 .9rem system-ui;padding:.75rem .9rem;white-space:nowrap;cursor:pointer}.tab[aria-selected=true]{color:var(--blue);border-color:var(--blue)}.panel[hidden]{display:none}.summary{display:grid;grid-template-columns:repeat(4,minmax(11rem,1fr));gap:.8rem;margin-bottom:1rem}.summary article,.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:0 1px 3px #17203308}.summary article{padding:.85rem 1rem}.summary small,.label{color:var(--muted);display:block}.summary strong{display:block;font:650 1.25rem var(--mono);margin-top:.15rem;letter-spacing:-.04em}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1rem}.card{padding:1rem;min-width:0}.card.wide{grid-column:span 2}.card h2{font-size:.95rem;margin:0 0 .85rem;letter-spacing:-.01em}.metrics{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.7rem}.metric{min-width:0;padding:.55rem .65rem;background:#f8fafc;border-radius:8px}.metric .label{font-size:.73rem}.metric .value{font:600 .94rem var(--mono);overflow-wrap:anywhere}.metric .detail{font-size:.73rem;color:var(--muted);margin-top:.1rem;overflow-wrap:anywhere}.warn{color:var(--warn)}.notice{padding:.8rem 1rem;border-left:3px solid var(--blue);background:#eaf2ff;color:#30496f;border-radius:0 8px 8px 0;margin-bottom:1rem}.config{display:flex;align-items:end;gap:.65rem;flex-wrap:wrap}.config label{display:grid;gap:.35rem;color:var(--muted);font-size:.8rem}.config input,.config button{height:2.55rem;border-radius:8px;border:1px solid #c8d4e2;padding:0 .75rem;font:inherit}.config input{min-width:15rem;background:#fff}.config button{border-color:var(--blue);background:var(--blue);color:#fff;font-weight:650;cursor:pointer}.config .result{color:var(--muted);font-size:.82rem}@media(max-width:900px){.shell{width:min(100% - 1.25rem,118rem);padding-top:1.25rem}.summary,.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.card.wide{grid-column:span 2}}@media(max-width:560px){header{display:block}.state{display:inline-block;margin-top:.8rem}.summary,.grid{grid-template-columns:1fr}.card.wide{grid-column:auto}.metrics{grid-template-columns:1fr}.config input{min-width:0;width:100%}}</style>
<body><div class="shell"><header><div><h1>DJI Transport Edge</h1><p>Observer only. Video, UDP and ROS continue if this page is unavailable.</p></div><div id="state" class="state waiting">connecting</div></header>
<nav class="tabs" aria-label="Dashboard sections"><button class="tab" data-tab="overview" aria-selected="true">Overview</button><button class="tab" data-tab="video" aria-selected="false">Video</button><button class="tab" data-tab="telemetry" aria-selected="false">Telemetry</button><button class="tab" data-tab="diagnostics" aria-selected="false">Diagnostics</button><button class="tab" data-tab="settings" aria-selected="false">Settings</button></nav>
<section class="panel" data-panel="overview"><div id="summary" class="summary"></div><div id="overview" class="grid"></div></section><section class="panel" data-panel="video" hidden><div id="video" class="grid"></div></section><section class="panel" data-panel="telemetry" hidden><div id="telemetry" class="grid"></div></section><section class="panel" data-panel="diagnostics" hidden><div id="diagnostics" class="grid"></div></section><section class="panel" data-panel="settings" hidden><div class="card wide"><h2>Clock destinations</h2><p class="notice">Add saved tablet IPv4 addresses, select exactly one for the next launch, or remove obsolete entries. This never interrupts the active transport.</p><form id="f" class="config"><label>Tablet IPv4<input id="ip" placeholder="192.168.1.151" inputmode="decimal"></label><button>Add IP</button><span id="result" class="result"></span></form><div id="hosts" class="hosts"></div></div></section></div>
<script>
const $ = selector => document.querySelector(selector);
const tabs = [...document.querySelectorAll('.tab')];
const names = {
  state: 'Transport state', ingress_state: 'RTP ingress', decoder_state: 'Decoder activity',
  ros_delivery_state: 'ROS image delivery', session: 'Session', telemetry_received: 'UDP received',
  metadata_received: 'Metadata received', telemetry_packets: 'Telemetry packets',
  telemetry_receiver_ready: 'Telemetry socket bound', telemetry_receiver_error: 'Telemetry socket error',
  metadata_receiver_ready: 'Metadata socket bound', metadata_receiver_error: 'Metadata socket error',
  clock_receiver_ready: 'Clock socket bound', clock_receiver_error: 'Clock socket error',
  telemetry_rejected: 'Rejected telemetry', metadata_rejected: 'Rejected metadata',
  clock_received: 'Clock packets', clock_rejected: 'Rejected clock', health_age_s: 'Health freshness',
  telemetry_age_s: 'Telemetry freshness', metadata_age_s: 'Metadata freshness', clock_age_s: 'Clock freshness',
  source_age_s: 'Freshness', received: 'Received', ingress_bitrate_bps: 'Edge ingress bitrate',
  ingress_bytes: 'Edge ingress bytes', ingress_packets: 'Edge RTP packets', decoded_count: 'Decoded frames',
  published_count: 'Published frames', decoded_fps: 'Decode FPS', published_fps: 'ROS publish FPS',
  superseded_frame_rate: 'Superseded frame rate', rtp_sequence_gaps: 'Finalized RTP gaps observed at Edge',
  rtp_pending_gaps: 'Pending RTP gaps', rtp_late_fills: 'Late-filled RTP gaps',
  rtp_sequence_wraps: 'RTP sequence wraps', rtp_ssrc_epochs: 'RTP SSRC epochs',
  rtp_duplicates: 'RTP duplicates', rtp_reordered: 'RTP reordered', rtp_malformed_headers: 'Malformed RTP',
  width: 'Resolution width', decoder: 'Decoder backend', decoder_reason: 'Decoder selection',
  pipeline_error: 'Pipeline error', position_valid: 'Position valid', rtk_valid: 'RTK active',
  heading_deg: 'Heading', altitude_m: 'Altitude', gimbal_pitch_deg: 'Gimbal pitch',
  gimbal_pitch_valid: 'Gimbal pitch valid', android_mono_ns: 'Android source time',
  attempts: 'Clock attempts', successes: 'Clock replies', timeouts: 'Clock timeouts',
  best_rtt_ns: 'Best RTT', offset_android_minus_edge_ns: 'Clock offset', ready: 'Clock ready',
  evidence_dropped: 'Evidence drops', evidence_write_errors: 'Evidence write errors',
  primary_rtp_bytes: 'Sender RTP bytes', telemetry_datagrams: 'Sender telemetry datagrams',
  telemetry_bytes: 'Sender telemetry bytes', metadata_datagrams: 'Sender metadata datagrams',
  metadata_bytes: 'Sender metadata bytes', clock_pongs_sent: 'Sender clock pongs'
};
function label(key) { return names[key] || key.replaceAll('_', ' '); }
function n(value, digits = 0) { return new Intl.NumberFormat(undefined, {maximumFractionDigits: digits}).format(value); }
function age(value) { return value == null || value < 0 ? 'not received' : value < 1 ? n(value * 1000) + ' ms' : n(value, 2) + ' s'; }
function bytes(value) { if (value == null) return 'not reported'; const units = ['B', 'kB', 'MB', 'GB']; let unit = 0; while (value >= 1000 && unit < 3) { value /= 1000; unit++; } return n(value, unit ? 2 : 0) + ' ' + units[unit]; }
function fmt(key, value) { if (value == null || value === '' || value === undefined) return key.includes('error') ? 'none' : 'not reported'; if (typeof value === 'boolean') return value ? 'yes' : 'no'; if (key.endsWith('_age_s')) return age(value); if (key.includes('bitrate')) return n(value / 1e6, 2) + ' Mb/s'; if (key.endsWith('_bytes')) return bytes(value); if (key.includes('rtt_ns') || key.includes('offset_')) return n(value / 1e6, 3) + ' ms'; if (key.endsWith('_fps') || key.endsWith('_rate') || key.endsWith('_deg')) return n(value, 2); if (Number.isInteger(value) && Math.abs(value) >= 1000) return n(value); return String(value); }
function metric(key, value, detail = '') { const element = document.createElement('div'); element.className = 'metric'; const warn = (key.includes('error') && value) || (key.includes('drops') && Number(value) > 0) || (key.includes('gaps') && Number(value) > 0); const labelElement = document.createElement('span'); labelElement.className = 'label'; labelElement.textContent = label(key); const valueElement = document.createElement('div'); valueElement.className = 'value' + (warn ? ' warn' : ''); valueElement.textContent = fmt(key, value); element.append(labelElement, valueElement); if (detail) { const detailElement = document.createElement('div'); detailElement.className = 'detail'; detailElement.textContent = detail; element.append(detailElement); } return element; }
function card(name, entries, wide = false) { const element = document.createElement('article'); element.className = 'card' + (wide ? ' wide' : ''); const heading = document.createElement('h2'); heading.textContent = name; const grid = document.createElement('div'); grid.className = 'metrics'; entries.forEach(([key, value, detail]) => grid.append(metric(key, value, detail))); element.append(heading, grid); return element; }
function put(id, ...items) { $('#' + id).replaceChildren(...items); }
function render(data) {
  const connection = data.connection || {}, sender = data.android_sender || {}, primary = data.primary_edge || {};
  const telemetry = data.telemetry || {}, sources = telemetry.sources || {}, flight = sources.flight || {};
  const rtk = sources.rtk || {}, gimbal = sources.gimbal || {}, clock = data.incident_clock || {};
  const state = primary.state || 'waiting', pill = $('#state');
  pill.textContent = state.replaceAll('_', ' '); pill.className = 'state ' + (state === 'publishing' ? '' : state.includes('error') ? 'error' : 'waiting');
  const summary = $('#summary'); summary.replaceChildren();
  [['Video', state], ['ROS image', primary.ros_delivery_state || 'not_publishing'], ['Navigation', telemetry.navigation_available ? 'available' : 'waiting'], ['Clock', clock.ready ? 'ready' : 'not synchronized']].forEach(([key, value]) => { const item = document.createElement('article'); const labelElement = document.createElement('small'); labelElement.textContent = key; const valueElement = document.createElement('strong'); valueElement.textContent = value; item.append(labelElement, valueElement); summary.append(item); });
  put('overview', card('Primary now', [['state', state, primary.state_reason], ['ingress_state', primary.ingress_state], ['decoder_state', primary.decoder_state], ['ros_delivery_state', primary.ros_delivery_state], ['published_fps', primary.published_fps], ['ingress_bitrate_bps', primary.ingress_bitrate_bps], ['width', primary.width, (primary.height || '?') + ' px high'], ['decoder', primary.decoder, primary.decoder_reason]], true), card('Transport link', [['session', connection.session], ['telemetry_received', connection.telemetry_received], ['metadata_received', connection.metadata_received], ['health_age_s', sender.health_age_s], ['metadata_age_s', primary.metadata_age_s], ['telemetry_packets', telemetry.telemetry_packets]]));
  put('video', card('Primary pipeline', [['state', state, primary.state_reason], ['ingress_state', primary.ingress_state], ['decoder', primary.decoder, primary.decoder_reason], ['decoder_state', primary.decoder_state], ['ros_delivery_state', primary.ros_delivery_state], ['width', primary.width, (primary.height || '?') + ' px high'], ['published_fps', primary.published_fps], ['decoded_fps', primary.decoded_fps], ['superseded_frame_rate', primary.superseded_frame_rate]], true), card('RTP ingress observations', [['ingress_packets', primary.ingress_packets], ['ingress_bytes', primary.ingress_bytes], ['ingress_bitrate_bps', primary.ingress_bitrate_bps], ['rtp_sequence_gaps', primary.rtp_sequence_gaps, 'Finalized after a 64-packet reorder horizon; not proof of network loss'], ['rtp_pending_gaps', primary.rtp_pending_gaps], ['rtp_late_fills', primary.rtp_late_fills], ['rtp_duplicates', primary.rtp_duplicates], ['rtp_reordered', primary.rtp_reordered], ['rtp_sequence_wraps', primary.rtp_sequence_wraps], ['rtp_ssrc_epochs', primary.rtp_ssrc_epochs], ['rtp_malformed_headers', primary.rtp_malformed_headers], ['pipeline_error', primary.pipeline_error]]));
  put('telemetry', card('Gimbal input', [['received', gimbal.received], ['source_age_s', gimbal.age_s], ['gimbal_pitch_valid', gimbal.gimbal_pitch_valid], ['gimbal_pitch_deg', gimbal.gimbal_pitch_deg], ['android_mono_ns', gimbal.android_mono_ns]]), card('RTK input', [['received', rtk.received], ['source_age_s', rtk.age_s], ['rtk_valid', rtk.rtk_valid], ['latitude_deg', rtk.latitude_deg], ['longitude_deg', rtk.longitude_deg], ['android_mono_ns', rtk.android_mono_ns]]), card('Flight input', [['received', flight.received], ['source_age_s', flight.age_s], ['position_valid', flight.position_valid], ['latitude_deg', flight.latitude_deg], ['longitude_deg', flight.longitude_deg], ['altitude_m', flight.altitude_m], ['heading_deg', flight.heading_deg], ['android_mono_ns', flight.android_mono_ns]]), card('Telemetry ingress', [['telemetry_packets', telemetry.telemetry_packets], ['telemetry_age_s', telemetry.telemetry_age_s], ['telemetry_received', connection.telemetry_received], ['telemetry_rejected', telemetry.telemetry_rejected], ['telemetry_receiver_ready', connection.telemetry_receiver_ready], ['telemetry_receiver_error', connection.telemetry_receiver_error], ['telemetry_peer_ipv4', connection.telemetry_peer_ipv4], ['metadata_received', connection.metadata_received], ['metadata_rejected', telemetry.metadata_rejected], ['metadata_receiver_ready', connection.metadata_receiver_ready], ['metadata_receiver_error', connection.metadata_receiver_error], ['metadata_peer_ipv4', connection.metadata_peer_ipv4]]), card('Android sender', Object.entries(sender.counters || {}), true), card('Android counters awaiting rollout', Object.entries(sender.optional_counters || {})));
  put('diagnostics', card('Clock synchronization', [['ready', clock.ready], ['attempts', clock.attempts], ['successes', clock.successes], ['timeouts', clock.timeouts], ['best_rtt_ns', clock.best_rtt_ns], ['offset_android_minus_edge_ns', clock.offset_android_minus_edge_ns], ['error', clock.error]], true), card('Evidence', [['evidence_dropped', clock.evidence_dropped], ['evidence_write_errors', clock.evidence_write_errors]]), card('UDP receivers', [['telemetry_receiver_ready', connection.telemetry_receiver_ready], ['telemetry_receiver_error', connection.telemetry_receiver_error], ['metadata_receiver_ready', connection.metadata_receiver_ready], ['metadata_receiver_error', connection.metadata_receiver_error], ['clock_receiver_ready', connection.clock_receiver_ready], ['clock_receiver_error', connection.clock_receiver_error], ['telemetry_rejected', telemetry.telemetry_rejected], ['metadata_rejected', telemetry.metadata_rejected], ['clock_received', telemetry.clock_received], ['clock_rejected', telemetry.clock_rejected], ['clock_age_s', telemetry.clock_age_s]]));
}
tabs.forEach(button => button.onclick = () => { tabs.forEach(tab => tab.setAttribute('aria-selected', tab === button)); document.querySelectorAll('.panel').forEach(panel => panel.hidden = panel.dataset.panel !== button.dataset.tab); });
async function poll() { try { render(await (await fetch('/v1/state', {cache: 'no-store'})).json()); } catch (error) { $('#state').textContent = 'dashboard unavailable'; $('#state').className = 'state error'; } }
$('#f').onsubmit = async event => { event.preventDefault(); const response = await fetch('/v1/config', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({android_clock_host: $('#ip').value})}); $('#result').textContent = response.ok ? 'Saved for next launch' : await response.text(); poll(); };
poll(); setInterval(poll, 1000);
</script></body></html>)";
constexpr const char * kClockHostsEnhancer = R"(<script>
async function loadClockConfig(){const r=await fetch('/v1/config',{cache:'no-store'});if(!r.ok)throw new Error('cannot load saved IPs');renderClockHosts(await r.json())}
function renderClockHosts(config){const root=document.querySelector('#hosts');root.replaceChildren();if(!config.clock_hosts.length){root.textContent='No saved tablet IPs yet.';return}config.clock_hosts.forEach(host=>{const row=document.createElement('div');row.style.cssText='display:flex;align-items:center;gap:.55rem;padding:.65rem .75rem;margin-top:.55rem;background:#f8fafc;border-radius:8px;font:500 .85rem ui-monospace,SFMono-Regular,Menlo,monospace';if(host===config.active_clock_host)row.style.outline='1px solid #9bc1f4';const name=document.createElement('span');name.textContent=host+(host===config.active_clock_host?' — selected for next launch':'');name.style.marginRight='auto';const use=document.createElement('button');use.type='button';use.textContent='Use';use.onclick=()=>saveClockConfig('use',host);const del=document.createElement('button');del.type='button';del.textContent='Delete';del.onclick=()=>saveClockConfig('delete',host);row.append(name,use,del);root.append(row)})}
async function saveClockConfig(action,host){const r=await fetch('/v1/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,host})});const data=await r.json();document.querySelector('#result').textContent=r.ok?'Saved for next launch':data.error;if(r.ok)renderClockHosts(data)}
Object.assign(names,{ingress_packets_window:'RTP packets this window',ingress_bytes_window:'RTP bytes this window',rtp_sequence_gaps_window:'RTP gaps this window',rtp_duplicates_window:'RTP duplicates this window',rtp_reordered_window:'RTP reordered this window',rtp_malformed_headers_window:'Malformed RTP this window',jitterbuffer_drop_messages:'Jitterbuffer drop reports',jitterbuffer_too_late:'Too-late RTP drops',jitterbuffer_drop_on_latency:'Latency-bound drops',jitterbuffer_malformed_messages:'Malformed drop reports',jitterbuffer_drop_messages_window:'Drop reports this window',jitterbuffer_too_late_window:'Too-late drops this window',jitterbuffer_drop_on_latency_window:'Latency drops this window',jitterbuffer_malformed_messages_window:'Malformed reports this window',send_failures:'Clock send failures',receive_failures:'Clock receive failures',decode_failures:'Invalid clock replies',unmatched_pongs:'Unmatched clock replies',last_rtt_ns:'Last RTT'});
const baseRender=render;render=data=>{baseRender(data);const p=data.primary_edge||{},cl=data.incident_clock||{};document.querySelector('#video').append(card('Jitterbuffer',[['jitterbuffer_drop_messages',p.jitterbuffer_drop_messages],['jitterbuffer_too_late',p.jitterbuffer_too_late],['jitterbuffer_drop_on_latency',p.jitterbuffer_drop_on_latency],['jitterbuffer_malformed_messages',p.jitterbuffer_malformed_messages],['jitterbuffer_drop_messages_window',p.jitterbuffer_drop_messages_window],['jitterbuffer_too_late_window',p.jitterbuffer_too_late_window],['jitterbuffer_drop_on_latency_window',p.jitterbuffer_drop_on_latency_window],['jitterbuffer_malformed_messages_window',p.jitterbuffer_malformed_messages_window]]));document.querySelector('#video').append(card('RTP window',[['ingress_packets_window',p.ingress_packets_window],['ingress_bytes_window',p.ingress_bytes_window],['rtp_sequence_gaps_window',p.rtp_sequence_gaps_window],['rtp_duplicates_window',p.rtp_duplicates_window],['rtp_reordered_window',p.rtp_reordered_window],['rtp_malformed_headers_window',p.rtp_malformed_headers_window]]));document.querySelector('#diagnostics').prepend(card('Clock outcomes',[['send_failures',cl.send_failures],['receive_failures',cl.receive_failures],['decode_failures',cl.decode_failures],['unmatched_pongs',cl.unmatched_pongs],['last_rtt_ns',cl.last_rtt_ns]]));document.querySelectorAll('.detail').forEach(e=>{if(e.textContent==='cumulative since session')e.textContent='cumulative since Edge process start'})};
document.querySelector('#f').onsubmit=async e=>{e.preventDefault();const input=document.querySelector('#ip');await saveClockConfig('add',input.value.trim());input.value=''};loadClockConfig().catch(e=>document.querySelector('#result').textContent=e.message);
</script>)";

std::string dashboard_page()
{
  std::string page(kPage);
  page.insert(page.rfind("</body>"), kClockHostsEnhancer);
  return page;
}

bool ipv4(const std::string & value) { in_addr address{}; return inet_pton(AF_INET, value.c_str(), &address) == 1; }
struct ClockConfig { std::string active; std::vector<std::string> hosts; };

ClockConfig read_clock_config(const std::string & path)
{
  ClockConfig result;
  std::ifstream input(path);
  std::string line;
  constexpr const char * marker = "# dashboard_clock_host: ";
  while (std::getline(input, line)) {
    if (line.rfind(marker, 0) == 0) {
      const auto host = line.substr(std::char_traits<char>::length(marker));
      if (ipv4(host)) result.hosts.push_back(host);
      continue;
    }
    const auto found = line.find("android_clock_host:");
    if (found == std::string::npos) continue;
    auto value = line.substr(found + std::string("android_clock_host:").size());
    value.erase(0, value.find_first_not_of(" \t\""));
    value.erase(value.find_last_not_of(" \t\"\r") + 1);
    if (ipv4(value)) result.active = value;
  }
  if (!result.active.empty()) result.hosts.push_back(result.active);
  std::sort(result.hosts.begin(), result.hosts.end());
  result.hosts.erase(std::unique(result.hosts.begin(), result.hosts.end()), result.hosts.end());
  return result;
}

bool write_clock_config(const std::string & path, const ClockConfig & config)
{
  std::ofstream output(path, std::ios::trunc);
  if (!output) return false;
  output << "# Managed by DJI Transport Edge dashboard.\n";
  for (const auto & host : config.hosts) output << "# dashboard_clock_host: " << host << "\n";
  output << "/dji_edge_transport:\n  ros__parameters:\n    android_clock_host: \"" << config.active << "\"\n";
  return static_cast<bool>(output);
}

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

std::string DashboardServer::config_json() const
{
  std::lock_guard<std::mutex> lock(mutex_);
  const auto config = read_clock_config(local_config_path_);
  return nlohmann::json{{"active_clock_host", config.active}, {"clock_hosts", config.hosts},
    {"applies", "next launch"}}.dump();
}

bool DashboardServer::update_config(const std::string & action, const std::string & host, std::string & error)
{
  if (!ipv4(host)) { error = "host must be an IPv4 address"; return false; }
  std::lock_guard<std::mutex> lock(mutex_);
  auto config = read_clock_config(local_config_path_);
  const auto existing = std::find(config.hosts.begin(), config.hosts.end(), host);
  if (action == "add") {
    if (existing == config.hosts.end()) config.hosts.push_back(host);
  } else if (action == "use") {
    if (existing == config.hosts.end()) { error = "add the IPv4 before selecting it"; return false; }
    config.active = host;
  } else if (action == "delete") {
    config.hosts.erase(std::remove(config.hosts.begin(), config.hosts.end(), host), config.hosts.end());
    if (config.active == host) config.active.clear();
  } else {
    error = "unsupported config action";
    return false;
  }
  std::sort(config.hosts.begin(), config.hosts.end());
  if (!write_clock_config(local_config_path_, config)) { error = "cannot write local config"; return false; }
  return true;
}

void DashboardServer::run()
{
  while (!stopping_.load()) { const int client = accept(socket_, nullptr, nullptr); if (client < 0) continue; char buffer[2048]{}; const auto count = recv(client, buffer, sizeof(buffer) - 1, 0); const std::string request(buffer, count > 0 ? count : 0);
    if (request.rfind("GET / ", 0) == 0) reply(client, 200, "text/html; charset=utf-8", dashboard_page());
    else if (request.rfind("GET /v1/state ", 0) == 0) reply(client, 200, "application/json", state_provider_());
    else if (request.rfind("GET /v1/config ", 0) == 0) reply(client, 200, "application/json", config_json());
    else if (request.rfind("POST /v1/config ", 0) == 0) { const auto body = request.substr(request.find("\r\n\r\n") + 4); try { const auto input = nlohmann::json::parse(body); const auto host = input.value("host", input.value("android_clock_host", "")); const auto action = input.value("action", "use"); std::string error; if (!update_config(action, host, error)) reply(client, 400, "application/json", nlohmann::json{{"error", error}}.dump()); else reply(client, 200, "application/json", config_json()); } catch (...) { reply(client, 400, "application/json", R"({"error":"invalid JSON"})"); } }
    else {
      reply(client, 404, "application/json", R"({"error":"not found"})");
    }
    close(client);
  }
}
}  // namespace dji_edge_transport
