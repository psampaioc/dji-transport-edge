Read 

Read 

Read , lines 1 to 10

Agora tenho uma visão completa do repositório. Vou te ensinar toda a arquitetura, stack e responsabilidades de cada parte.

---

## 📦 Visão Geral do Projeto

**edge-matrice-transport** é o **receiver Ubuntu** para transporte de telemetria e vídeo H.264 do DJI Matrice (via tablet Galaxy Tab S9 + Cendence). Ele **não controla o drone** — apenas recebe, valida, mapeia relógios, grava evidência e retransmite RTP para GStreamer.

### Stack Tecnológica

| Camada | Tecnologia |
|--------|------------|
| **Linguagem** | Python 3.11+ (sem dependências runtime — só stdlib) |
| **Transporte** | UDP (telemetria, metadados, clock, RTP) + TCP (HTTP state) |
| **Video Pipeline** | GStreamer 1.0 (externo via `gst-launch-1.0`) |
| **Configuração** | TOML (`config.toml` local, `config.example.toml` versionado) |
| **Evidência** | NDJSON (append-only) + RTP binário opcional |
| **Testes** | `unittest` (stdlib) |
| **Deploy** | systemd unit template + script `edge_receiver.sh` |

---

## 🏗️ Arquitetura de Alto Nível

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DJI Edge Receiver (Ubuntu)                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   Telemetry  │  │ Frame Meta   │  │    Clock     │  │    RTP       │   │
│  │   :5500/UDP  │  │   :5501/UDP  │  │   :5502/UDP  │  │  :5600/:5610 │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │                 │            │
│         ▼                 ▼                 ▼                 ▼            │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    PacketIngestor (validação central)               │   │
│  │  • decode_json_packet / parse_rtp_packet                            │   │
│  │  • SequenceTracker (gaps, duplicates, out-of-order)                │   │
│  │  • ClockMapper (NTP-style Android↔Edge offset)                     │   │
│  └────────────────────────────┬────────────────────────────────────────┘   │
│                               │                                            │
│         ┌─────────────────────┼─────────────────────┐                     │
│         ▼                     ▼                     ▼                     │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐              │
│  │   Latest    │      │  Evidence   │      │   RtpRelay  │              │
│  │   State     │      │   Writer    │      │  (per feed) │              │
│  │  (thread-   │      │  (async,    │      │  • validate │              │
│  │   safe)     │      │   bounded)  │      │  • relay    │              │
│  └──────┬──────┘      └──────┬──────┘      └──────┬──────┘              │
│         │                    │                    │                      │
│         ▼                    ▼                    ▼                      │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              StateHttpServer (:8088/TCP)                        │   │
│  │  • GET /health  → process health, GStreamer status, evidence    │   │
│  │  • GET /v1/state → full snapshot (flight, RTK, gimbal, video,   │   │
│  │                    clock, transport stats, packet stats)        │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              GStreamer (external processes)                     │   │
│  │  udpsrc:5602/5612 → rtpjitterbuffer → rtph264depay → h264parse  │   │
│  │  → avdec_h264 → videoconvert → queue(leaky=1) → fakesink        │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 Estrutura de Arquivos e Responsabilidades

### Core Package: `dji_edge_receiver`

| Arquivo | Responsabilidade |
|---------|------------------|
| `__init__.py` | Versão do pacote (`0.1.0`) |
| `cli.py` | Entry point `dji-edge-receiver` com 3 subcomandos: `run`, `print-gstreamer`, `validate-packet` |
| `config.py` | Dataclasses frozen + validação TOML → `ReceiverConfig` (network, storage, video_streams) |
| `protocol.py` | **Parsing & validação** de pacotes JSON v1 + RTP RFC 3550/6184. `Packet`, `RtpPacket`, `ProtocolError` |
| `clock.py` | `ClockMapper` — NTP-style offset estimation (median of lowest-RTT samples) |
| `state.py` | `LatestState` (thread-safe latest snapshot), `SequenceTracker` (modulus opcional para RTP 16-bit) |
| `evidence.py` | `EvidenceWriter` — writer assíncrono com queue bounded, NDJSON + RTP binário, manifest por sessão |
| `video.py` | `RtpRelay` — valida RTP, conta access units (marker bit), inspeciona H.264 passivamente, relaya UDP→localhost |
| `h264.py` | `H264RtpAnalyzer` — parse SPS (dimensões), conta NALs (SPS/PPS/IDR), reassembly FU-A/STAP-A bounded |
| `server.py` | `EdgeReceiver` — orquestra tudo: endpoints UDP, ClockService, RtpRelay[], StateHttpServer |

### Scripts: `scripts`

| Script | Função |
|--------|--------|
| `capture_transport_bench.py` | **Benchmark props-off**: polla `/v1/state` 1Hz por N segundos, gera relatório JSON com deltas (vídeo, sender, receiver, clock, associações frame↔telemetria) |
| `edge_receiver.sh` | Gerenciador de processo background (start/status/stop) com PID file, log, verificação de portas via `ss` |
| `protocol_fixtures.py` | Gera/valida golden packets para testes de conformidade |
| `verify_edge_receiver.py` | Verificação rápida de saúde do receiver |

### Testes: `tests`

| Teste | Cobertura |
|-------|-----------|
| `test_protocol.py` | JSON v1 (compact + fragmentado), clock, RTP header, rejeições |
| `test_config.py` | Validação TOML (nomes canônicos, portas únicas, intervalos) |
| `test_h264.py` | SPS real DJI (1280×720), STAP-A, FU-A, discontinuidades, IDR age |
| `test_clock_state.py` | ClockMapper, SequenceTracker (wrap 16-bit), LatestState (fragmented samples, frame association) |
| `test_integration.py` | End-to-end: telemetry + frame_meta + RTP + HTTP state |
| `test_bench_report.py` | Valida lógica de deltas e taxas do `capture_transport_bench.py` |

### Configuração

| Arquivo | Descrição |
|---------|-----------|
| `config.example.toml` | Template versionado (headless, `fakesink`) |
| `config.toml` | **Local, gitignored** — edite `android_clock_host` com IP do tablet |
| `edge-receiver.toml.example` | Template para deploy systemd (evidence_dir relativo a runtime) |

### Evidência: `evidence/<session>/`

Cada sessão cria diretório com:
- `manifest.json` — schema version, session ID, timestamp criação, formato RTP binário
- `telemetry.ndjson` — todos pacotes telemetria (flight, rtk, gimbal, health, hello)
- `frame_metadata.ndjson` — sidecars `video_au` / `frame_meta`
- `clock.ndjson` — amostras clock_ping/pong + estimativas
- `video-<feed>-<ssrc>.rtpbin` — **binário**: `u64 edge_mono_ns + u32 len + payload` (se `capture_rtp=true`)

---

## 🔄 Fluxo de Dados Detalhado

### 1. Receção UDP (4 endpoints independentes)
```python
# server.py - UdpEndpoint threads
telemetry      → :5500  → PacketIngestor.ingest("telemetry", ...)
frame_metadata → :5501  → PacketIngestor.ingest("frame_metadata", ...)
clock          → :5502  → ClockService._handle (ping/pong)
RTP primary    → :5600  → RtpRelay._run (validate + relay → :5602)
RTP secondary  → :5610  → RtpRelay._run (validate + relay → :5612)
```

### 2. Validação de Pacote JSON (`protocol.py:decode_json_packet`)
- Tamanho ≤ `max_datagram_bytes` (default 1200)
- UTF-8 JSON válido, root = object
- `v` = 1, `type` ∈ {telemetry, flight, rtk, gimbal, health, hello, frame_meta, video_au, clock_ping, clock_pong}
- `session` non-empty, `stream`/`feed`/`source` → normalizado
- `seq`/`frame_seq` monotônico por `(session, type, stream)`
- Timestamps Android `rx_mono_ns` / `android_mono_ns` > 0
- **Fragmentado**: `sample_sequence`, `chunk_index`, `chunk_count`, `fields` — só publica quando todos chunks chegam
- **Clock**: `t0`, `t1`, `t2` ordenados, RTT ≥ 0

### 3. Sequence Tracking (`state.py:SequenceTracker`)
- Sem modulus: gap = `seq - prev - 1`, out-of-order se `seq < prev`
- Com modulus (RTP 16-bit): distance modular, gap/duplicate/out-of-order
- Disposições: `first`, `ok`, `gap`, `duplicate`, `out_of_order`, `timestamp_regression`

### 4. Clock Mapping (`clock.py:ClockMapper`)
```
Edge send (t0) ──────► Android recv (t1) ──────► Android send (t2) ──────► Edge recv (t3)
     │                    │                       │                       │
     └────────────────────┴───────────────────────┴───────────────────────┘
                              RTT = (t3-t0) - (t2-t1)
                              offset = ((t1-t0) + (t2-t3)) / 2
```
- Guarda até 64 amostras, usa **mediana das 8 menores RTTs**
- `android_to_edge(android_ns)` = `android_ns - offset` (retorna `None` se não pronto)

### 5. LatestState (`state.py:LatestState`)
- Thread-safe com `Lock`
- Fontes: `flight`, `rtk`, `gimbal`, `health` → latest por stream
- Frames: `video_au` / `frame_meta` → latest por feed
- **Associação causal frame→telemetria**: para cada frame, acha telemetria anterior mais recente (por `android_mono_ns`)
- Histórico bounded (512 por source) para associação
- Contadores de transporte: accepted, rejected, duplicates, gaps, RTP stats

### 6. RTP Relay + H.264 Inspection (`video.py:RtpRelay` + `h264.py:H264RtpAnalyzer`)
- Valida header RTP v2, payload type esperado, CSRC, extensão, padding
- SequenceTracker modulus=65536 por SSRC
- **Access units**: conta quando `marker=1` (RFC 6184)
- **H.264 passivo**: parse SPS (dimensões, profile, level), conta SPS/PPS/IDR, reassembly FU-A/STAP-A bounded (1MB max)
- **Relay**: `sendto(data, 127.0.0.1:pipeline_port)` — **bytes inalterados**
- GStreamer lançado como subprocesso; falha no startup = erro imediato

### 7. Evidence Writer (`evidence.py:EvidenceWriter`)
- Thread dedicada + `Queue(16384)` — drop explícito se cheia (contador `dropped_records`)
- NDJSON: `edge_receive_mono_ns`, `edge_receive_utc_ns`, `remote`, `raw_utf8`, `raw_base64`, `parsed`, `validation`
- RTP binário: `u64 edge_mono_ns (BE) + u32 len (BE) + bytes`
- `fsync` opcional por registro

### 8. HTTP State (`server.py:StateHttpServer`)
- `ThreadingHTTPServer` em `:8088` (localhost only por default)
- `/health` → `{status: ok|degraded, evidence: {...}, video: [...]}`
- `/v1/state` → snapshot completo + `receiver_health`

---

## 🎯 Pontos-Chave de Design

| Princípio | Implementação |
|-----------|---------------|
| **Zero-copy relay** | RTP bytes validados → `sendto()` direto para GStreamer; Python nunca decodifica |
| **Bounded everything** | Queue evidence (16k), history (512/source), clock samples (64), marker timestamps (120) |
| **Failure observable** | `/health` expõe: evidence drops/write_errors, GStreamer exited, relay errors, kernel socket drops |
| **No ROS dependency** | Pure Python + GStreamer externo; ROS 2 adapter virá depois (appsink in-process) |
| **Canonical feeds** | Só `primary` / `secondary` aceitos; `fpv` rejeitado no config |
| **Fragmented telemetry** | Android envia chunks; edge só publica sample completo (`complete_samples` counter) |
| **Clock ≠ exposure** | Android `elapsedRealtimeNanos()` = callback arrival, **não** camera exposure time |

---

## 🚀 Como Rodar (Resumo)

```bash
# 1. Deps sistema
sudo apt install python3-venv gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav

# 2. Venv + install
python3 -m venv .venv
.venv/bin/pip install -e .

# 3. Config local
cp config.example.toml config.toml
# Edite config.toml: android_clock_host = "192.168.50.20" (IP do tablet)

# 4. Testes
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/protocol_fixtures.py --check
.venv/bin/dji-edge-receiver print-gstreamer --config config.toml

# 5. Run (foreground)
.venv/bin/dji-edge-receiver run --config config.toml

# 6. No tablet: Start transport → IPv4 do Ubuntu

# 7. Observar
curl http://127.0.0.1:8088/health | python3 -m json.tool
curl http://127.0.0.1:8088/v1/state | python3 -m json.tool

# 8. Bench props-off (outro terminal)
mkdir -p .runtime/bench
.venv/bin/python scripts/capture_transport_bench.py --duration-s 60 \
  --out .runtime/bench/transport-$(date -u +%Y%m%dT%H%M%SZ).json
```

---

## 📊 Métricas-Chave no `/v1/state`

### Por feed de vídeo (`receiver_health.video[].stats`):
| Métrica | Significado |
|---------|-------------|
| `packets_received` / `bytes_received` | Volume RTP real |
| `access_units_observed` | Frames delimitados por marker bit RTP |
| `access_unit_bytes_avg` / `max` | Tamanho aproximado frames |
| `estimated_fps` / `estimated_bitrate_bps` | Calculados no edge |
| `sequence_gaps` / `duplicates` / `out_of_order` | Qualidade transporte UDP |
| `kernel_socket_drops` | **Deve ficar 0** — drops na fila UDP do kernel |
| `socket_receive_buffer_bytes` | Tamanho efetivo da fila UDP (4MB configurado) |
| `h264.stream_info.width/height` | Resolução do SPS recebido |
| `h264.sps_count`, `pps_count`, `idr_count` | Parâmetros codec + keyframes |
| `h264.last_idr_age_ms` | Tempo desde último IDR (recuperação) |
| `h264.fragment_resets`, `sps_parse_errors` | Descontinuidades FU-A / SPS inválido |

### Telemetria (`packet_stats`):
- `rtk`, `flight`, `gimbal`, `health`: `datagrams`, `bytes`, `min/max_bytes`, `complete_samples`
- RTK fragmentado: `complete_samples` só incrementa quando **todos chunks** chegam

---

## 🔮 Próximos Passos (Roadmap)

1. ✅ Freeze project boundary
2. ✅ Feed names canônicos (`primary`/`secondary`)
3. ✅ Schema v1 documentado (`PROTOCOL_V1.md`)
4. ✅ Harden validation (clock, portas, payload)
5. ✅ Failure observable (`/health`, evidence drops, GStreamer exit)
6. ✅ Decode boundary decision: **GStreamer externo agora, appsink in-process no ROS adapter**
7. 🔄 **Benchmark real hardware** (30 min, FPV, RTK válido, restart/recovery)
8. ⏳ ROS 2 adapter (`appsink` → `sensor_msgs/Image` + tópicos telemetria)
9. ⏳ Secure deployment (interface dedicada, firewall, auth opcional)

---

## 🧪 Testes Existentes

```bash
# Todos os testes
.venv/bin/python -m unittest discover -s tests -v

# Específicos
.venv/bin/python -m unittest tests.test_protocol -v
.venv/bin/python -m unittest tests.test_h264 -v
.venv/bin/python -m unittest tests.test_clock_state -v
.venv/bin/python -m unittest tests.test_integration -v
```

O teste de integração (`test_integration.py`) sobe um `EdgeReceiver` real em portas efêmeras, injeta telemetria + frame_meta + RTP sintético, e valida o snapshot HTTP.

---

## 📝 Notas Importantes

1. **`config.toml` é gitignored** — cada máquina tem a sua; `config.example.toml` é o template
2. **`evidence_dir` relativo ao config file** — `config.toml` na raiz → `evidence`; deploy template usa `../.runtime/edge-evidence`
3. **GStreamer falha no startup** se plugin faltar (ex: `h264parse`, `avdec_h264`) — não finge que está rodando
4. **Secondary/FPV**: callbacks existem no Android, mas últimas evidências mostram `secondary_access_units=0` e `secondary_rtp_packets=0` — bloqueio no Android parser/assembler
5. **RTK solution**: presença de callback ≠ solução válida; última evidência tinha `positioning_solution: "NONE"` / satélites 0
6. **Decoded frames**: `decoded_frame_count_available: false` — contagem real virá no `appsink` do ROS adapter

---