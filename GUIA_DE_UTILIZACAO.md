# Guia de utilização

```mermaid
flowchart LR
    A[Drone M210 RTK V2] -->|OcuSync| B[Cendence]
    B -->|USB| C[Galaxy Tab S9]
    C --> D[DJI MSDK V4 callbacks]
    D --> E1[H.264 Annex-B]
    D --> E2[Flight / RTK / Gimbal]
    E1 --> F1[Access units + RTP packetizer]
    E2 --> F2[JSON v1 fragmentado]
    F1 -->|Primary UDP 5600<br/>Secondary UDP 5610| G[Ubuntu edge receiver]
    F2 -->|Telemetry 5500<br/>Frame metadata 5501<br/>Clock 5502| G
    G --> H1[Validação, sequência,<br/>clock e evidência]
    G -->|RTP inalterado<br/>localhost 5602/5612| H2[GStreamer]
    H2 --> I1[rtpjitterbuffer]
    I1 --> I2[rtph264depay]
    I2 --> I3[h264parse]
    I3 --> I4[H.264 decoder]
    I4 --> J[fakesink pré-ROS]
    H1 --> K[/v1/state e /health]
    J -. etapa futura .-> L[appsink no nó ROS 2]
    L -.-> M[sensor_msgs/Image]
    H1 -. etapa futura .-> N[Tópicos RTK / gimbal / flight]
```

Ordem real do caminho de dados:

1. O drone envia vídeo e estado ao Cendence por OcuSync.
2. O Cendence entrega os dados ao tablet por USB.
3. O aplicativo Android recebe bytes H.264 e callbacks de telemetria pelo DJI MSDK V4.
4. Android monta access units H.264 Annex-B e envia RTP/UDP sem re-encoding.
5. Android envia Flight, RTK e Gimbal como JSON v1 independente do FPS do vídeo.
6. O receiver Ubuntu valida os pacotes, mede perdas, mapeia relógios e grava evidência.
7. O receiver retransmite RTP validado, byte por byte, para portas locais.
8. GStreamer faz jitter buffering, depayload, parsing e decode H.264.
9. Na configuração headless de exemplo, o decode termina em `fakesink`; na configuração visual local usada na bancada, termina em `ximagesink`. Ambos servem para validar o decode, não para alimentar ainda o detector.
10. Depois da validação props-off, `fakesink` será substituído por `appsink` dentro do nó ROS 2.

## 1. O que este projeto faz

Este projeto roda no laptop Ubuntu. Ele não contém DJI SDK e não envia comandos ao drone. Ele recebe o transporte iniciado manualmente no tablet, mantém estado recente, grava evidência e prepara o vídeo para o futuro adapter ROS 2.

## 2. Portas padrão

| Porta | Direção | Conteúdo |
| --- | --- | --- |
| `5500/UDP` | Android → Ubuntu | Telemetria JSON v1 |
| `5501/UDP` | Android → Ubuntu | Sidecar de cada access unit de vídeo |
| `5502/UDP` | bidirecional | Ping/pong de relógio monotónico |
| `5600/UDP` | Android → Ubuntu | RTP/H.264 primary |
| `5610/UDP` | Android → Ubuntu | RTP/H.264 secondary |
| `5602/UDP` | localhost | Relay primary para GStreamer |
| `5612/UDP` | localhost | Relay secondary para GStreamer |
| `8088/TCP` | localhost | HTTP `/health` e `/v1/state` |

## 3. Preparação inicial no Ubuntu

Execute uma vez no Ubuntu:

```bash
cd /home/psampaioc/Workspaces/edge-matrice-transport
sudo apt install python3-venv gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav
python3 -m venv .venv
.venv/bin/pip install -e .
```

Depois da instalação, use sempre os executáveis dentro de `.venv` para evitar misturar dependências do sistema com as do projeto.

Confirme os elementos necessários:

```bash
for element in udpsrc rtpjitterbuffer rtph264depay h264parse avdec_h264 videoconvert fakesink ximagesink; do
  gst-inspect-1.0 "$element" >/dev/null && echo "$element OK" || echo "$element AUSENTE"
done
```

Neste host, a inspeção atual encontrou os elementos necessários para o caminho software, incluindo `h264parse`, `avdec_h264` e `ximagesink`. A configuração local `config.toml` é ignorada pelo Git e está preparada para abrir uma janela por feed; `config.example.toml` permanece headless com `fakesink`.

### Instalar como aplicativo global

Depois de concluir a configuração local, instale este projeto como o `detection-verification`: o comando ficará disponível em qualquer diretório, sem ativar `.venv`.

```bash
uv tool install --force /home/psampaioc/Workspaces/edge-matrice-transport
dji-edge init-config --from /home/psampaioc/Workspaces/edge-matrice-transport/config.toml
```

O segundo comando cria `~/.config/dji-edge-receiver/config.toml` uma única vez e preserva o destino atual de `evidence/`. Depois disso, de qualquer pasta:

```bash
dji-edge
```

`dji-edge-receiver` é o nome completo equivalente. Os subcomandos, como `dji-edge run` e `dji-edge print-gstreamer`, continuam disponíveis para operação técnica. A prioridade da configuração é: `--config /caminho/config.toml`, variável `DJI_EDGE_RECEIVER_CONFIG`, `./config.toml` no diretório atual e, por fim, `~/.config/dji-edge-receiver/config.toml`.

### Referência completa de comandos e flags

`dji-edge` e `dji-edge-receiver` são o mesmo aplicativo. O primeiro é apenas o nome curto. Onde aparece `CONFIG`, informe um caminho absoluto ou relativo para um arquivo TOML.

| Comando | O que faz | Flags e opções |
| --- | --- | --- |
| `dji-edge` | Comando normal do operador. É idêntico a `dji-edge dashboard`: inicia receiver, dashboard e as duas pipelines/janelas nativas configuradas. | `--config CONFIG`: usa este TOML em vez da configuração encontrada automaticamente. `--no-browser`: inicia tudo, mas não abre o navegador; o URL continua sendo mostrado no terminal. `-h`/`--help`: mostra a ajuda deste modo. |
| `dji-edge dashboard` | Forma explícita do comando normal. Útil em scripts para deixar claro que se deseja a interface gráfica. | As mesmas flags `--config CONFIG`, `--no-browser`, `-h` e `--help`. |
| `dji-edge run` | Inicia somente o receiver: UDP, relógio, evidência, HTTP e pipelines GStreamer. Não abre dashboard nem navegador. Use para systemd/headless. | `--config CONFIG`: TOML a usar. `-h`/`--help`: ajuda. |
| `dji-edge print-gstreamer` | Não inicia nada. Imprime os comandos `gst-launch-1.0` que seriam usados para Primary e Secondary. | `--config CONFIG`: TOML a inspecionar. `-h`/`--help`: ajuda. |
| `dji-edge validate-packet PATH` | Não abre pipelines. Valida um datagrama JSON NDJSON salvo no arquivo `PATH` contra o contrato do transporte. Retorna JSON e código 2 quando inválido. | `PATH`: arquivo obrigatório. `--version N`: versão do protocolo esperada; padrão `1`. `--max-bytes N`: tamanho máximo aceito para o datagrama; padrão `1200`. `-h`/`--help`: ajuda. |
| `dji-edge init-config` | Cria a configuração persistente que permite executar o app de qualquer diretório. Recusa sobrescrever um arquivo existente. | `--from SOURCE`: copia um TOML que já funciona; recomendado, pois preserva as suas portas, IP do tablet, sinks e caminho de evidência. `--path PATH`: destino alternativo; o padrão é `~/.config/dji-edge-receiver/config.toml`. Sem `--from`, cria o template headless incluído no aplicativo. `-h`/`--help`: ajuda. |

#### Como o aplicativo encontra a configuração

Ao executar `dji-edge` ou qualquer subcomando que use receiver, a primeira fonte existente nesta ordem vence:

1. `--config CONFIG` informado na linha de comando.
2. A variável de ambiente `DJI_EDGE_RECEIVER_CONFIG`. Exemplo temporário: `DJI_EDGE_RECEIVER_CONFIG=/tmp/bancada.toml dji-edge`.
3. Um `config.toml` na pasta em que o comando foi executado.
4. `~/.config/dji-edge-receiver/config.toml`, criado por `init-config`.

Portanto, no uso normal não há flags: use somente `dji-edge`. Use `--config` apenas para uma bancada alternativa, sem modificar a configuração principal.

## 4. Criar a configuração local

```bash
cd /home/psampaioc/Workspaces/edge-matrice-transport
if [ ! -f config.toml ]; then cp config.example.toml config.toml; fi
```

Edite `config.toml` e defina `android_clock_host` com o IPv4 do tablet na rede local dedicada:

```toml
[network]
android_clock_host = "192.168.50.20"
```

Mantenha `http_host = "127.0.0.1"`. Não exponha o estado HTTP na rede sem uma decisão explícita de segurança.

Para uma sessão longa, considere `capture_rtp = false`: telemetria e metadados continuam registrados, mas o disco não recebe todo o vídeo RTP bruto.

## 5. Verificar antes de conectar o tablet

```bash
cd /home/psampaioc/Workspaces/edge-matrice-transport
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/protocol_fixtures.py --check
.venv/bin/dji-edge-receiver print-gstreamer --config config.toml
```

O último comando apenas mostra os pipelines que serão iniciados.

## 6. Iniciar a aplicação de medição

Para a bancada visual, este é o único comando necessário. Ele inicia o receiver, abre o dashboard local e abre as duas janelas GStreamer configuradas (Primary e Secondary):

```bash
.venv/bin/dji-edge-receiver dashboard --config config.toml
```

O endereço do dashboard é mostrado no terminal, normalmente `http://127.0.0.1:8090`. Se o navegador não abrir, copie esse endereço. O receiver continua em execução e as janelas de vídeo continuam sendo nativas; não há vídeo recodificado no navegador.

No dashboard:

1. Em **Ubuntu addresses**, use o botão **Copy `<IPv4>`**. Ele copia o IPv4 local do laptop para a área de transferência e mostra uma confirmação. Esse é o endereço a preencher no transporte do tablet — não é o IP do drone nem um endereço público.
2. Confira o IP do tablet em **Tablet clock address**. Para alterá-lo, edite apenas esse campo e escolha **Save and restart receiver**. O receiver para e volta a iniciar; se a nova configuração falhar, o arquivo anterior e o receiver anterior são restaurados.
3. O seletor **Raw RTP evidence** controla `storage.capture_rtp`: em **On**, o receiver grava os datagramas RTP validados em `evidence/<sessão>/video-*.rtpbin`; em **Off**, vídeo ao vivo, telemetria e metadados continuam, mas esses arquivos brutos deixam de ser gravados. A mudança só entra em vigor ao escolher **Save and restart receiver**.
4. Observe Primary e Secondary separadamente. `waiting` significa que nenhum RTP chegou naquele feed; não indica falha do outro feed. **GStreamer video windows** identifica as duas janelas nativas separadas, onde o vídeo de baixa latência aparece; o dashboard é a interface de medição no navegador e não recodifica vídeo.
5. **Receiver status: healthy** significa que o receiver/evidência estão saudáveis. Não significa, por si só, que o tablet está enviando vídeo: confira o estado e os contadores de cada feed.
6. Escolha **Start measurement** para gerar o relatório em `.runtime/bench/` sem abrir outro terminal. A duração fica agrupada à esquerda; **Stop** gera um relatório parcial válido.
7. Para fechar a aplicação inteira pelo navegador, escolha **Exit transport** e confirme. Isso encerra receiver, dashboard e as duas janelas GStreamer, e pede que o navegador feche a própria aba/janela. Alguns navegadores bloqueiam fechar uma aba aberta manualmente; nesse caso ela mostra somente a confirmação de que o transporte terminou e pode ser fechada sem deixar serviço ativo. Fechar somente uma janela de vídeo ou a aba do navegador não é equivalente.

O modo antigo, sem dashboard, continua disponível para systemd ou uso headless:

```bash
.venv/bin/dji-edge-receiver run --config config.toml
```

Se `h264parse` ou outro plugin estiver ausente, o receiver falha no início em vez de fingir que o decoder está ativo.

## 7. Iniciar o transporte no Android

Com aeronave desmontada/props-off e o Cendence conectado:

1. Abra o aplicativo Matrice no Tab S9.
2. Entre em **Deployment Transport → Video + Telemetry to Edge**.
3. Informe o IPv4 do laptop Ubuntu.
4. Pressione **Start transport**.

O transporte só começa após essa ação visível. Nenhum comando de voo é emitido pelo edge.

## 8. Observar o estado

O dashboard substitui estes comandos durante a bancada normal. Eles continuam úteis para diagnóstico automatizado ou headless:

```bash
curl http://127.0.0.1:8088/health | python3 -m json.tool
curl http://127.0.0.1:8088/v1/state | python3 -m json.tool
```

`/health` mostra:

- processo GStreamer por feed: `running`, `exited` ou `disabled`;
- erros no relay local;
- estado da thread de evidência;
- registros descartados ou erros de escrita.

`/v1/state` inclui Flight, RTK, gimbals, últimos frames, associação causal frame/telemetria, relógio, estatísticas RTP e o mesmo `receiver_health`.

Em cada item de `receiver_health.video[].stats`, observe:

- `packets_received` e `bytes_received`: volume RTP real por feed;
- `access_units_observed`: número de frames RTP delimitados pelo marker bit;
- `access_unit_bytes_avg`/`access_unit_bytes_max`: tamanho aproximado dos frames transportados;
- `estimated_fps` e `estimated_bitrate_bps`: estimativas calculadas no edge;
- `sequence_gaps`, `duplicates` e `out_of_order`: qualidade do transporte UDP.
- `kernel_socket_drops`: pacotes descartados pela fila UDP do kernel Linux; deve ficar em zero;
- `socket_receive_buffer_bytes`: tamanho efetivo da fila UDP concedida pelo Linux;
- `h264.stream_info.width`/`height`: resolução declarada no SPS H.264 recebido;
- `h264.sps_count`, `pps_count` e `idr_count`: presença dos parâmetros do codec e keyframes;
- `h264.last_idr_age_ms`: tempo desde o último IDR observado; ajuda a medir recuperação;
- `h264.fragment_resets` e `sps_parse_errors`: descontinuidades de FU-A ou SPS inválido.

Essa inspeção H.264 é passiva: lê somente cabeçalhos/NALs no payload RTP e nunca altera os bytes retransmitidos. `access_units_observed` conta access units delimitadas pelo marker RTP, não frames que chegaram até a saída do decoder. Por isso `decoded_frame_count_available` continua `false`; a contagem de frames realmente decodificados será feita no futuro `appsink` do adaptador ROS 2.

Em `packet_stats`, observe `rtk`, `flight`, `gimbal` e `health`: `datagrams`, bytes, menor/maior datagrama e `complete_samples`. Para RTK fragmentado, `complete_samples` só aumenta quando todos os chunks de uma amostra foram recebidos.

## 9. Capturar uma bancada props-off

Esta etapa é um relatório de medição, não um segundo receiver e não um gravador de MP4. Com o dashboard, use **Start measurement** e o arquivo aparecerá automaticamente em `.runtime/bench/`. O script abaixo permanece como alternativa headless; ele consulta `http://127.0.0.1:8088/v1/state` uma vez por segundo e salva um retrato inicial, os retratos seguintes e um resumo comparativo.

### Onde criar a pasta

`.runtime/bench` fica dentro da raiz do projeto edge:

```text
/home/psampaioc/Workspaces/edge-matrice-transport/.runtime/bench/
```

Crie-a em qualquer terminal livre, sem parar o receiver:

```bash
mkdir -p .runtime/bench
```

O comando acima só funciona se o terminal estiver na raiz do edge. Para evitar dúvida, use o caminho completo:

```bash
mkdir -p /home/psampaioc/Workspaces/edge-matrice-transport/.runtime/bench
```

### Como executar

Deixe o receiver rodando, o transporte ligado no tablet e execute em outro terminal:

```bash
cd /home/psampaioc/Workspaces/edge-matrice-transport
.venv/bin/python scripts/capture_transport_bench.py --duration-s 60 \
  --out .runtime/bench/transport-$(date -u +%Y%m%dT%H%M%SZ).json
```

Usar explicitamente `.venv/bin/python` evita depender do bit executável do arquivo e funciona mesmo se o repositório tiver sido copiado por um meio que perdeu permissões Unix.

O arquivo JSON é criado dentro de `/home/psampaioc/Workspaces/edge-matrice-transport/.runtime/bench/`. O nome inclui a data/hora UTC e o script recusa sobrescrever um arquivo existente.

### O que ele captura

O relatório captura medições do estado observado durante a janela solicitada:

- saúde do receiver e dos dois processos GStreamer;
- pacotes RTP e bytes por feed;
- access units observadas pelo marker bit RTP;
- tamanho médio/máximo das access units;
- FPS e bitrate estimados;
- gaps, duplicatas e pacotes fora de ordem;
- contadores enviados pelo Android, como callbacks, drops e erros de socket;
- datagramas/bytes de `flight`, `rtk`, `gimbal` e `health`;
- quantidade de amostras RTK fragmentadas que chegaram completas;
- estado do clock Android↔Ubuntu;
- último frame e associação causal com a telemetria anterior.

No `summary`, use principalmente os campos com sufixo `_delta`: eles representam somente a janela capturada, e não todo o tempo desde que o receiver iniciou. `video_delta` traz volume, FPS, bitrate, gaps e drops de socket por feed; `sender_delta` traz os incrementos dos contadores Android; `packet_stats_delta` traz as amostras de telemetria recebidas durante a medição. `video_metrics` e `packet_stats` continuam sendo os totais absolutos no último retrato.

Ele não captura diretamente:

- um arquivo MP4;
- uma imagem decodificada por frame;
- exposição/capture time da câmera;
- dados que nunca chegaram ao Ubuntu.

O vídeo RTP bruto, quando `capture_rtp = true`, é gravado separadamente em `evidence/<sessão>/video-*.rtpbin` pelo receiver. O relatório apenas registra os contadores e tamanhos desse fluxo. Se o Android tiver `secondary_video_callbacks` mas o edge tiver zero `secondary.packets_received` e zero `secondary.access_units_observed`, a falha ocorreu antes do edge e o relatório serve justamente para demonstrar isso.

Critérios mínimos da bancada:

1. `primary.stats.access_units_observed` deve crescer continuamente e `estimated_fps` deve deixar de ser `null`.
2. Depois de selecionar/ativar o FPV, `secondary.stats.access_units_observed` e `secondary.stats.bytes_received` também devem crescer. Se `secondary_video_callbacks` crescer no Android mas esses valores permanecerem zero, o bloqueio ainda está no Android parser/assembler.
3. `packet_stats.rtk.complete_samples` deve crescer; depois, confirme no conteúdo de `v1/state` que a solução RTK é válida e não apenas `NONE`.
4. Durante a sessão, `sequence_gaps`, `duplicates`, `out_of_order`, drops e erros devem permanecer zero ou ser explicados.

Para testar recuperação, pare o receiver com `Ctrl+C`, confirme que as janelas fecham, inicie-o novamente e repita a sessão. O serviço systemd também está configurado com `Restart=on-failure`; o estado deve denunciar `pipeline_status=exited` em vez de esconder uma falha do GStreamer.

## 10. Parar

Primeiro pressione **Stop transport** no tablet. Depois, se iniciou em background:

```bash
scripts/edge_receiver.sh stop
```

Em primeiro plano, use `Ctrl+C`.

## 11. Evidência NDJSON

NDJSON é usado para auditoria, não para o caminho live. Ele é adequado porque é append-only, legível e tolera um registro incompleto no fim do arquivo.

Cada sessão contém telemetria, sidecars, clock e erros. Captura RTP é opcional e deve ser desligada quando não for necessária para evitar carga de disco.

## 12. Limites atuais

- A ponte ROS 2 Humble agora existe em `ros2_ws/`, dentro do container `dji-edge-ros:humble`. Ela publica navegação, telemetria normalizada, diagnóstico e imagens `primary`/`fpv` via `appsink`; o guia específico está em [`ros2_ws/README.md`](ros2_ws/README.md).
- Nesta primeira integração, o receiver continua sendo o processo nativo responsável por validar UDP/RTP, clock e evidência. O container consome `GET /v1/state` e as portas RTP locais `5602`/`5612`; não há `shmsink` nem depayload H.264 em Python.
- O timestamp do vídeo é chegada no Android, não exposição da câmera.
- UDP não autentica o remetente; use rede/interface dedicada e firewall.
- A configuração aceita apenas os feeds canónicos `primary` e `secondary`.
- O primary já foi visto decodificando no Ubuntu. O secondary/FPV ainda entrega callbacks no Android, mas as últimas evidências têm `secondary_access_units=0` e `secondary_rtp_packets=0`; portanto não há vídeo FPV no edge para visualizar.
- A presença de callback RTK ainda não prova solução RTK válida; a última evidência observada tinha solução `NONE`/satélites 0.
- `config.toml` pode abrir duas janelas, mas não garante que ambas terão imagem: isso depende de cada feed produzir access units válidas.

## 13. ROS 2 Humble e o mapa

O workspace ROS é deliberadamente separado do ROS instalado no Ubuntu. O Docker
usa `network_mode: host`, portanto pode falar com o receiver local (`127.0.0.1:8088`)
e com os relays RTP sem copiar vídeo por disco ou memória partilhada.

```text
Android → receiver nativo → estado HTTP + RTP local → container Humble → ROS 2
```

O mapa leve existente foi copiado para `ros2_ws/src/dji_edge_mapper/config/map_vis.pcd`.
O mapa original, os arquivos PLY/PCD grandes e os scripts legados permanecem no
workspace de mapeamento original sem alteração.

Para construir a imagem:

```bash
cd /home/psampaioc/Dockerfiles
DJI_EDGE_WS="$HOME/Workspaces/edge-matrice-transport/ros2_ws" docker compose build dji_edge_humble
```

Para iniciar a ponte depois de iniciar `dji-edge` no host:

```bash
djiedge
source /opt/ros/humble/setup.bash
colcon build --symlink-install
source install/setup.bash
ros2 launch dji_edge_bridge dji_edge_bringup.launch.py
```

Em um segundo container execute `ros2 launch dji_edge_mapper dji_edge_mapper.launch.py`.
Os tópicos e a interpretação RTK/GPS estão documentados em
[`ros2_ws/README.md`](ros2_ws/README.md). O FPV só publicará imagens quando o
Android efetivamente enviar o feed secundário para 5610; ele não é presumido
como funcional apenas porque o tópico ROS existe.

O trabalho pendente e seus critérios de aceite estão em [`docs/ROADMAP.md`](docs/ROADMAP.md).
