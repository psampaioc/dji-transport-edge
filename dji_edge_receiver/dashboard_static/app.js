const $ = (id) => document.getElementById(id);
const fmt = (value, unit = '') => value === null || value === undefined ? '—' : `${value}${unit}`;
const fact = (label, value) => `<div class="datum"><b>${value}</b><span>${label}</span></div>`;
function renderFacts(target, items) { target.innerHTML = items.map(([label, value]) => fact(label, value)).join(''); }

function renderFeeds(feeds) {
  const grid = $('feed-grid'); const template = $('feed-template'); grid.innerHTML = '';
  feeds.forEach((feed) => {
    const node = template.content.firstElementChild.cloneNode(true); node.classList.add(feed.status);
    node.querySelector('.feed-name').textContent = feed.name.toUpperCase();
    node.querySelector('.feed-status').textContent = feed.status.replace('_', ' ');
    node.querySelector('.badge').textContent = feed.pipeline_status;
    node.querySelector('.feed-explanation').textContent = feed.explanation;
    const resolution = feed.resolution ? `${feed.resolution.width}×${feed.resolution.height}` : 'not known';
    node.querySelector('.feed-metrics').innerHTML = [
      ['RTP packets', fmt(feed.counters.packets_received)], ['AU rate', fmt(feed.estimated_fps, ' fps')],
      ['Bitrate', feed.estimated_bitrate_bps ? `${Math.round(feed.estimated_bitrate_bps / 1000)} kbps` : '—'],
      ['Resolution', resolution], ['Packet age', fmt(feed.last_packet_age_ms, ' ms')], ['RTP gaps', fmt(feed.counters.sequence_gaps)],
    ].map(([label, value]) => fact(label, value)).join('');
    node.querySelector('.feed-boundary').textContent = feed.decoded_frame_count_available
      ? 'Decoded frame count available.' : (feed.measurement_boundary || 'Decoded frame count is unavailable in this RTP relay.');
    grid.append(node);
  });
}

async function copyIp(address) {
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(address);
    else {
      const field = document.createElement('textarea'); field.value = address; field.style.position = 'fixed';
      document.body.append(field); field.select(); document.execCommand('copy'); field.remove();
    }
    $('copy-message').textContent = `${address} copied. Paste it into the tablet transport IP field.`;
  } catch { $('copy-message').textContent = `Select and copy this address manually: ${address}`; }
}

function receiverLabel(status) {
  return {ok: 'Receiver status: healthy', degraded: 'Receiver status: degraded', stopped: 'Receiver status: stopped', running: 'Receiver status: starting'}[status] || `Receiver status: ${status || 'unknown'}`;
}

function render(data) {
  const result = data.result || {}; const receiver = data.receiver_status || result.status || 'unknown';
  $('receiver-status').textContent = receiverLabel(receiver);
  $('receiver-dot').className = `dot ${receiver === 'ok' ? 'receiving' : receiver === 'degraded' || receiver === 'stopped' ? 'error' : ''}`;
  $('updated-at').textContent = `Last dashboard refresh: ${new Date().toLocaleTimeString()}`;
  renderFeeds(data.feeds || []);
  if (data.network && document.activeElement !== $('tablet-ip')) $('tablet-ip').value = data.network.tablet_clock_ip || '';
  const addresses = data.network?.local_ipv4_addresses || []; $('local-ips').innerHTML = '';
  if (addresses.length) addresses.forEach((address) => {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'ip-copy';
    button.textContent = `Copy ${address}`; button.title = `Copy ${address} to the clipboard`; button.onclick = () => copyIp(address);
    $('local-ips').append(button);
  }); else $('local-ips').innerHTML = '<span class="hint">No non-loopback IPv4 discovered.</span>';
  const config = data.configuration || {};
  if (data.configuration && document.activeElement !== $('capture-rtp')) {
    $('capture-rtp').checked = Boolean(config.capture_rtp); $('capture-state').textContent = config.capture_rtp ? 'On' : 'Off';
  }
  renderFacts($('session-config'), [['Receiver bind', fmt(data.network?.receiver_bind_host)], ['Evidence', fmt(config.storage_evidence_dir)], ['RTP capture', config.capture_rtp ? 'on' : 'off']]);
  const clock = data.timing?.clock || {};
  renderFacts($('timing'), [['Clock', clock.ready ? 'ready' : 'unavailable'], ['Samples', fmt(clock.sample_count)], ['Best RTT', fmt(clock.best_rtt_ns && Math.round(clock.best_rtt_ns / 1e6), ' ms')], ['Primary delay', fmt(data.timing?.video?.primary?.callback_to_edge_ms, ' ms')], ['Secondary delay', fmt(data.timing?.video?.secondary?.callback_to_edge_ms, ' ms')]]);
  const rtk = data.telemetry?.rtk?.data || {}; const health = data.telemetry?.android_health?.data || {};
  renderFacts($('telemetry'), [['Flight sample', data.telemetry?.flight ? 'received' : '—'], ['RTK sample', data.telemetry?.rtk ? 'received' : 'unavailable'], ['Gimbals', fmt((data.telemetry?.gimbals || []).length)], ['FC callback', fmt(health.callback_hz?.['flight_controller:0'], ' Hz')], ['RTK fix', fmt(rtk.fix_status || rtk.status)], ['Primary callbacks', fmt(health.primary_video_callbacks)], ['Secondary callbacks', fmt(health.secondary_video_callbacks)]]);
  const evidence = data.impact?.evidence || {}; const transport = data.impact?.receiver_transport || {};
  renderFacts($('impact'), [['Evidence queue', fmt(evidence.queue_depth)], ['Evidence drops', fmt(evidence.dropped_records)], ['Write errors', fmt(evidence.write_errors)], ['RTP packets', fmt(transport.rtp_packets)], ['RTP gaps', fmt(transport.rtp_sequence_gaps)], ['Sender video drops', fmt((health.primary_video_callback_drops || 0) + (health.secondary_video_callback_drops || 0))]]);
  const bench = data.bench;
  if (bench) $('bench-result').textContent = bench.status === 'complete' || bench.status === 'stopped' ? `${bench.status}\n${bench.path}\n${JSON.stringify(bench.summary?.video_delta || {}, null, 2)}` : JSON.stringify(bench, null, 2);
  if (result.message) $('apply-message').textContent = result.message;
}

async function request(path, body) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Edge-Dashboard': '1'}, body: JSON.stringify(body || {})});
  const data = await response.json(); if (!response.ok) throw new Error(data.message || 'Request failed'); return data;
}
async function refresh() {
  try { render(await (await fetch('/api/status', {cache: 'no-store'})).json()); }
  catch { $('receiver-status').textContent = 'Receiver status: dashboard unavailable'; $('receiver-dot').className = 'dot error'; }
}
$('capture-rtp').onchange = () => { $('capture-state').textContent = $('capture-rtp').checked ? 'On' : 'Off'; };
$('apply-config').onclick = async () => {
  try { const result = await request('/api/config', {android_clock_host: $('tablet-ip').value, capture_rtp: $('capture-rtp').checked}); $('apply-message').textContent = result.message; await refresh(); }
  catch (error) { $('apply-message').textContent = error.message; }
};
$('bench-start').onclick = async () => { try { await request('/api/bench/start', {duration_s: $('bench-duration').value, interval_s: 1}); await refresh(); } catch (error) { $('bench-result').textContent = error.message; } };
$('bench-stop').onclick = async () => { try { await request('/api/bench/stop'); await refresh(); } catch (error) { $('bench-result').textContent = error.message; } };
$('shutdown').onclick = async () => {
  if (!window.confirm('Exit DJI Transport Edge? This stops the receiver and closes both GStreamer video windows.')) return;
  $('shutdown').disabled = true; $('shutdown').textContent = 'Stopping…';
  try {
    await request('/api/shutdown');
    window.close();
    window.setTimeout(() => {
      if (!window.closed) {
        document.title = 'DJI Transport Edge — stopped';
        document.body.innerHTML = '<main class="closed-dashboard"><p class="eyebrow">DJI / UBUNTU EDGE</p><h1>Transport stopped</h1><p>The browser blocked automatic tab closing. You can close this tab now; receiver and GStreamer are already stopped.</p></main>';
      }
    }, 300);
  }
  catch (error) { $('apply-message').textContent = error.message; $('shutdown').disabled = false; $('shutdown').textContent = 'Exit transport'; }
};
refresh(); setInterval(refresh, 1000);
