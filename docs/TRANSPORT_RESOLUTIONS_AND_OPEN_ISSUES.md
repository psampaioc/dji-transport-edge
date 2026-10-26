# Resoluções e questões abertas — transporte Android → Edge

Atualizado em 2026-10-26. Este documento é a fonte de acompanhamento para as
hipóteses de estabilidade/latência do Primary. Ele separa fatos observados de
decisões e de verificações ainda pendentes. O repositório Edge não muda o
contrato Android sem validação conjunta com o SDK.

## Resumo executivo para revisão

O sistema tem **três modos de falha observáveis**, que não devem ser tratados
como uma causa única:

```text
1. Aircraft/Cendence → tablet cai
   AIRCRAFT_LINK disconnect → binding Primary é desanexado → hub/listener não recebe vídeo.

2. Tablet continua sinalizando progresso, mas o Edge não recebe RTP
   health ou metadata Android recentes + RTP stale no Edge.
   Limite: ainda não distingue socket RTP Android de Wi-Fi/rota.

3. Edge recebe RTP, mas não decodifica uma imagem completa
   RTP fresco + decoded frame stale → H.264 aguarda próximo IDR.
   Isto é compatível com pacote/referência H.264 perdida ou chegada tarde.
```

O modo 1 explica a última parada deliberada, quando o tablet foi desconectado
do Cendence. Os modos 2 e 3 explicam as travadas anteriores e recorrentes. Não
há evidência, na sessão ativa abaixo, de que o hub DJI seja a causa principal
dessas travadas recorrentes.

### Limite de atribuição

Nenhuma métrica atual prova, sozinha, a causa física de uma lacuna RTP. A
evidência localiza a primeira fronteira conhecida que deixou de progredir:

| Primeira fronteira parada | O que isso prova | O que ainda não prova |
|---|---|---|
| `AIRCRAFT_LINK` / binding DJI | a ligação aircraft→tablet ou seu ciclo de vida SDK caiu | defeito específico no rádio, cabo, Cendence ou SDK |
| callback do hub DJI | não chegou vídeo utilizável ao hub/listener | se a causa foi feed DJI, binding ou elo anterior |
| AU/RTP Android | o transport Android não produziu saída a partir de vídeo recebido | se foi parser, fila ou `send()` sem métricas pareadas |
| RTP no Edge | o datagrama chegou ao processo Edge | que formou um frame H.264 decodificável |
| frame decodificado | GStreamer/decoder produziu pixels | que ROS/RViz os exibiu sem atraso |

`rtp_sequence_gaps` é cumulativo. Ele é evidência de descontinuidade de
sequência, **não** uma percentagem de perda Wi-Fi sem janelas de tempo e início
de contadores alinhados.

O tracker atual mantém uma janela fixa de 64 sequências: gaps recentes ficam
provisórios e podem ser preenchidos por pacotes reordenados. Só os gaps ainda
abertos ao sair da janela ou ao terminar uma época SSRC entram no contador
finalizado. Esses números continuam sendo observações no ingresso do Edge, não
prova de onde ocorreu uma perda. Os valores históricos desta sessão foram
capturados pelo algoritmo anterior, que finalizava gaps imediatamente, e não
são recalculados por esta alteração.

## Sessão analisada mais recentemente (2026-09-21)

Esta é a sessão usada para investigar especificamente as travadas recorrentes
do Primary. A interrupção permanente ao fim foi provocada pelo operador ao
desligar/desconectar o drone; ela é preservada como contexto, mas foi removida
da análise causal das travadas intermediárias.

### Artefatos preservados

| Artefato | Papel | Limite importante |
|---|---|---|
| Diagnóstico Android — `transport-a260fe2a.ndjson` (artefato local, não incluído no repositório) | fonte para callbacks DJI, AUs, RTP aceito no socket Android, telemetria e serviço | uma amostra compacta de `health` por aproximadamente 1 s; não substitui uma captura por pacote RTP |
| Evidência Edge — `edge-0.ndjson` (artefato local, não incluído no repositório) | transições de saúde Primary, contadores RTP recebidos e drops do jitter buffer | registra transições, não cada datagrama; esta versão ainda não grava timestamp Edge em cada transição |
| Diagnóstico original Android no tablet | origem imutável copiada por ADB/USB | `/sdcard/Android/data/com.dji.sdk.sample/files/transport/<session-id>/transport.ndjson` |

Os dois primeiros artefatos ficam em `.runtime/`, fora do Git por serem
evidência de uma sessão concreta. O identificador local da sessão foi omitido
deste documento versionado.

### Fatos observados durante a parte ativa

| Medida | Evidência | Conclusão que podemos tirar |
|---|---|---|
| Android produziu Primary | 695 registros `health`; antes da parada final, normalmente 29–31 AUs/s e aproximadamente 500–730 pacotes RTP/s | na granularidade de 1 s, o sender Android continuou a produzir e aceitar RTP durante a janela analisada |
| Android não reportou falha local | `primary_video_callback_drops=0`, `video_socket_errors=0`, `rtp_errors=0`, fila de vídeo sem chunks descartados | não há prova de congestionamento ou erro no sender Android; isto não prova entrega na rede após `DatagramSocket.send()` |
| Edge entrou repetidamente em ausência de RTP | 16 transições intermediárias para `waiting_for_udp`; em cada uma `ingress_packet_window=0` | por mais de um segundo de cada vez, nenhum RTP Primary chegou ao `udpsrc` do Edge |
| Sequência RTP perdeu continuidade no Edge | `rtp_sequence_gap_count` cresceu de 0 para 64.226 em ciclos de recuperação, tipicamente saltos de 3.356–4.478 sequências | a trava visual coincide com descontinuidade RTP observada **antes** do jitter buffer e do decoder |
| Jitter buffer também descartou atraso | `jitterbuffer_drop_on_latency_count=1.895`, `too_late_count=0` | chegadas irregulares/bursts excederam o limite de 20 ms; esses descartes preservam baixa latência, mas não explicam sozinhos 64.226 sequências ausentes |
| Decoder recuperou sem erro fatal | após os ciclos, houve `primary_recovery`/`publishing`; não há `pipeline_error` | GStreamer/NVIDIA/ROS são afetados pelo fluxo incompleto, não são a primeira prova de perda |

#### Linha de evidência do Edge

Os seguintes saltos são os contadores cumulativos nas recuperações sucessivas
do Edge, excluindo a última interrupção permanente: `10 → 3.698 → 7.710 →
11.929 → 15.407 → 19.622 → 24.100 → 28.422 → 32.738 → 36.874 → 40.913 →
45.129 → 49.283 → 53.015 → 56.878 → 60.711 → 64.067 → 64.226`.

Cada ciclo teve a mesma forma operacional:

```text
RTP chega e o Primary publica
  → Edge fica mais de 1 s sem datagrama RTP (`waiting_for_udp`)
  → RTP retorna com salto de sequência de milhares de pacotes
  → decoder aguarda um IDR quando necessário
  → Primary volta a publicar o frame recente
```

Isto explica exatamente o comportamento relatado: imagem boa por alguns
segundos, congelamento/sumiço, e retorno direto ao frame atual em vez de
acumular vídeo antigo.

### O que a sessão exclui e o que ainda não separa

| Hipótese | Estado | Por quê |
|---|---|---|
| Backlog de ROS ou RViz como origem dos gaps | **Excluída como primeira causa** | a continuidade RTP é medida no pad de entrada `udpsrc`, antes de decode, `appsink`, ROS e RViz |
| Jitter buffer criar os gaps RTP | **Excluída** | o rastreador observa o cabeçalho RTP antes do `rtpjitterbuffer`; o jitter buffer apenas descarta datagramas atrasados depois de recebidos |
| Falha final do vídeo causada pelo Edge | **Excluída para esta sessão** | a parada final segue o desligamento/desconexão deliberado do drone; depois dela telemetria/health continuaram, mas callbacks/AUs/RTP Android ficaram estáveis |
| Hub DJI parar repetidamente durante os freezes | **Não demonstrada nesta resolução temporal** | não há intervalo ≥2 s sem progresso RTP Android antes da parada final nos pulsos de 1 Hz; pausas menores que 1 s e bursts internos continuam possíveis |
| Android aceitar RTP mas a rede não entregar | **Compatível e provável, mas não provada isoladamente** | Android conta `send()` aceito; Edge vê lacunas reais. Falta comparar a sequência RTP atribuída no Android com a mesma sequência capturada no Ubuntu |
| Packetizer Android pular sequência antes do envio | **Ainda possível** | o log Android atual não registra a continuidade da sequência RTP de saída por janela; o contador de pacotes aceitos não equivale a continuidade de números de sequência |

### Conclusão operacional atual

O `rtp_sequence_gap_count` **não causa** a trava: ele é o instrumento que
prova que os datagramas RTP não chegaram continuamente ao Edge. A causa física
permanece numa de duas fronteiras que a instrumentação atual ainda não separa:

1. o packetizer/sender Android salta sequências ou deixa de emitir datagramas;
2. os datagramas saem do Android, mas são perdidos/retidos entre tablet e
   `udpsrc` do Ubuntu (Wi-Fi, hotspot/roteamento, interface ou buffers UDP).

Não trocar RTP/UDP por TCP resolve esta evidência: TCP esconderia lacunas por
retransmissão e introduziria *head-of-line blocking*, portanto entregaria vídeo
antigo justamente quando o requisito é o frame mais recente.

### Próximo experimento que decide a fronteira

Durante uma trava real, registrar simultaneamente, sem alterar o contrato:

1. **Android/SDK:** sequência RTP atribuída pelo packetizer imediatamente antes
   de `send()`, incluindo delta, máximo de datagrama e duração de `send()`;
2. **Ubuntu/Edge:** sequência RTP observada na entrada UDP, preferencialmente
   com PCAP iniciado explicitamente pelo operador ou um contador temporário por
   janela;
3. marcar início/fim visual da trava com wall-clock para alinhar os dois logs.

Interpretação:

- sequência já salta no Android: investigar packetizer/encaminhamento Android;
- sequência contínua no Android, mas salta no Ubuntu: investigar Wi-Fi,
  hotspot/roteamento e buffers de recepção;
- sequência contínua nos dois, mas o decoder espera IDR: investigar a
  associação de Access Units/H.264 e a cadência dos IDRs.

## Sessão de referência mais recente — `076257a5-…`

Fontes preservadas:

```text
Android: .runtime/android-import/076257a5-816f-46e3-b3f7-9fdb269b81a9/transport.ndjson
Edge:    .runtime/evidence/076257a5-816f-46e3-b3f7-9fdb269b81a9/edge-0.ndjson
```

### Resultado Android durante a janela ativa

| Sinal local do tablet | Medição | Leitura correta |
|---|---:|---|
| Duração ativa antes da desconexão aircraft | ~20 min | houve uma janela longa com produção de vídeo Android antes da parada final |
| Callbacks Primary no listener | 308.201 | continuaram durante a parte ativa; não há pausa prolongada do hub nessa janela de 1 Hz |
| Access Units | 35.877; mediana de 31,747 ms | aproximadamente 31,5 AU/s; cadência nominal próxima de 30 FPS |
| RTP aceito pelo socket Android | 553.235 pacotes; 597.124.630 bytes | o sender aceitou tráfego local enquanto a produção estava ativa |
| Bitrate Android por janela de health | mediana 4,00 Mb/s; p95 4,62 Mb/s; máximo 5,34 Mb/s | não há pico de 10× nesta captura de 1 Hz |
| IDR | 1.122 | aproximadamente um IDR por segundo, suficiente para recuperação em princípio, mas não para evitar os freezes de referência perdida |
| Parser | Annex-B; 0 resets, 0 descontinuidades, 0 NALs malformadas | o parser local não mostrou corrupção do fluxo que recebeu |
| Socket/filas vídeo | 0 erros RTP, 0 drops de chunk; high-water da fila 27 | não há prova de descarte local; high-water não é, por si só, falha |
| Escritor NDJSON/serviço | writer vivo, 0 drops; foreground service `RUNNING` | o diagnóstico permaneceu confiável durante o ensaio |

### Incidentes realmente vistos no Edge antes da desconexão final

O arquivo de evidência do Edge contém **77 `primary_incident` e 76
`primary_recovery`**. O incidente sem recuperação é a parada final após o
tablet/Cendence ser desconectado.

| Tipo Edge | Quantidade | O que aconteceu | Limite da conclusão |
|---|---:|---|---|
| `waiting_for_idr` | 50 | RTP ainda era recente, mas nenhum frame havia sido decodificado por mais de 1 s | não identifica qual pacote/referência se perdeu; não culpa GPU automaticamente |
| `waiting_for_udp` com health Android recente | 14 | o Android ainda enviava health, mas RTP ficou stale no Edge | health não prova que o pacote RTP específico saiu do socket Android |
| `waiting_for_udp` com metadata recente | 13 | metadados de Primary chegaram ao Edge, mas RTP ficou stale | aponta a fronteira RTP; ainda separa imperfeitamente socket Android de Wi-Fi |

Portanto, as travadas visuais foram registradas antes da desconexão final. O
fato de a recuperação ocorrer em seguida, muitas vezes quando chega um IDR,
explica o comportamento "trava → pula para o frame atual → volta".

### Parada final — evento separado

Esta sessão contém **exatamente dois** eventos `AIRCRAFT_LINK`: um
`Aircraft connection observed` no início e **um único**
`Aircraft disconnection observed` no encerramento. Ela contém **exatamente um**
`DJI_PRIMARY_BINDING_DETACHED`, também no encerramento. Não há detach/rebind
durante a janela ativa de vídeo; portanto estes eventos não explicam as pausas
observadas antes de o operador desligar/desconectar o drone.

No encerramento deliberado, o Android registrou, nesta ordem:

1. `AIRCRAFT_LINK: Aircraft disconnection observed`;
2. `DJI_PRIMARY_BINDING_DETACHED` **0,504 s** depois;
3. `PRIMARY_TRANSPORT_LISTENER_STALE` / `DJI_CALLBACK_STALLED` aproximadamente
   1,4 s depois.

Após isso, health e telemetria continuaram, mas callbacks DJI, AUs, RTP e
metadata ficaram parados. Este é um problema **upstream do transport Edge**;
não deve ser confundido com as pausas RTP/H.264 anteriores.

### Estado Edge após a parada

Na última leitura após a parada: ingress bitrate 0, ingress/decode/publish
stale, 0 FPS, `pipeline_error` vazio e `dropped_old_frames` zero. Há muitos
gaps RTP cumulativos e poucas reordenações; não há pacote RTP malformado. Isso
descarta backlog ROS e crash GStreamer como causa da ausência final, mas não
descarta perda/atraso de H.264 durante a fase ativa.

### Clock nesta sessão

O clock foi intermitente: 79 respostas para 787 tentativas na leitura final.
Ele pode alternar entre pronto e não pronto sem afetar a recepção de imagem.
Enquanto não houver uma resposta recente, `clock ready` deve ser falso e a
sincronização precisa frame↔navegação não pode ser declarada disponível.

## Dashboard: o que cada campo significa

O dashboard é observador loopback (`127.0.0.1:8090`) e faz `GET /v1/state` a
cada segundo. Ele não participa do caminho de vídeo. A partir desta revisão,
o estado mostrado é calculado pela mesma avaliação de saúde usada pelo nó ROS,
e não apenas pelo fato histórico de já ter recebido algum frame.

### Estado do Primary

| Campo mostrado | Atualiza quando | Significado |
|---|---|---|
| `Transport state` | a cada resposta do estado | resultado da cadeia inteira: `waiting_for_udp`, `waiting_for_keyframe`, `waiting_for_idr`, `publishing`, `pipeline_error` ou `ros_delivery_stalled` |
| `RTP ingress` | a cada resposta | `receiving` somente quando RTP é recente; `stale` ou `not_receiving` caso contrário |
| `Decoder activity` | a cada resposta | `decoding` somente quando um frame decodificado é recente; `waiting_for_idr` quando há RTP recente mas não há imagem nova; `no_recent_input` quando não há entrada; `error` se GStreamer reportar erro |
| `ROS image delivery` | a cada resposta | `publishing` somente quando uma imagem ROS foi publicada recentemente; `stale` quando a última publicação envelheceu |
| `Edge ingress bitrate`, `Decode FPS`, `ROS publish FPS` | timer de diagnóstico, normalmente 1 s | taxa da última janela; zero significa que não houve progresso naquela janela, não que o contador foi apagado |
| idades (`*_age_s`) | a cada resposta | tempo desde o último evento correspondente; é a evidência de atualidade mais importante |

### Campos que são acumulados ou última observação

| Grupo | Campos | Interpretação |
|---|---|---|
| Acumulados desde início do processo/session | bytes, pacotes, frames, gaps RTP, tentativas/sucessos/timeouts clock, erros e drops | devem ser comparados por delta numa janela, não pelo número absoluto |
| Último valor conhecido | resolução, peer/IP, sessão, navegação, health counters Android | ficam visíveis após uma interrupção; verificar a idade correspondente antes de tratá-los como atuais |
| Selecionados no start da pipeline | `Decoder backend` (`nvidia` ou `cpu`) e razão de seleção | mostram o backend escolhido, não uma garantia eterna de que está produzindo frames |
| Estimativas históricas clock | melhor RTT e offset | permanecem após uma amostra; só `clock ready` e idade da última resposta declaram validade atual |
| Configuração para próximo launch | IP de clock salvo | não altera o processo em execução |

### Verificação do decoder NVIDIA sem interferir no stream

O Edge não reinicia nem troca automaticamente o decoder ao primeiro frame
ausente: fazer isso aumentaria indisponibilidade e esconderia a causa. A
verificação passiva é suficiente e não cria callbacks no hot path:

1. o backend selecionado é registrado quando a pipeline inicia;
2. o bus GStreamer é consultado pelo ciclo de status já existente;
3. qualquer erro de `nvh264dec` aparece como `pipeline_error` e
   `Decoder activity: error`;
4. se RTP continuar chegando mas frames não saírem, o estado é
   `waiting_for_idr`, não uma acusação falsa à GPU;
5. se não houver entrada RTP, o estado é `no_recent_input`.

Assim, `nvidia + decoding` significa que o backend NVIDIA está realmente
produzindo frames recentes. `nvidia + waiting_for_idr` significa que ele é o
backend selecionado, mas não recebeu uma sequência H.264 recuperável; não é
uma prova de falha da GPU.

## Arquitetura que mantemos no Edge

```text
DJI VideoFeeder H.264 no tablet
  → RTP/UDP :5600
  → rtpjitterbuffer (20 ms, descarta atraso)
  → depay + parse + NVIDIA decode (CPU somente fallback)
  → appsink (1 frame, drop=true)
  → /dji/primary/image_raw (ROS 2)
```

Não há `videoscale`, preview GStreamer, FFmpeg, OpenCV, `shmsink` ou relay HTTP
no caminho quente. `videoconvert` produz BGR porque `sensor_msgs/Image` precisa
de pixels CPU; essa é a cópia final inevitável no contrato ROS atual.

## 1. Encoding e packetização do Android

| Ponto | Estado e evidência | Decisão | Próxima verificação / dono |
|---|---|---|---|
| `MediaFormat.KEY_BITRATE_MODE` VBR/CBR | **Não aplicável ao transporte atual.** O app não usa `MediaCodec`/`MediaFormat` para reencodar. Ele recebe H.264 já codificado do DJI `VideoFeeder` e o encaminha. | Não adicionar um encoder Android só para forçar CBR: acrescentaria latência, calor e uma falha nova. O bitrate/resolução devem ser controlados no perfil de vídeo DJI, se o aircraft/câmera expuserem essa opção. | SDK: registrar o perfil DJI efetivamente ativo e o bitrate observado do H.264 enviado. |
| Intra-refresh versus I-frame | **Ainda desconhecido.** Nesta sessão, o Edge registrou 50 incidentes em que havia RTP recente, mas o decoder aguardava uma Access Unit H.264 recuperável/IDR, e 76 recuperações de incidente no total. Isto é compatível com perda de referências e/ou keyframes pouco frequentes; não prova qual das duas. | Manter `wait-for-keyframe=true`; nunca reiniciar o pipeline por uma perda normal. Solicitar IDR frequente é a forma correta de recuperação. | SDK/DJI: verificar se a câmera expõe intervalo de IDR. Meta operacional: IDR ao menos a cada 0,5–1 s. `KEY_INTRA_REFRESH_PERIOD` não é aplicável sem MediaCodec próprio. |
| Tamanho RTP/FU-A | **Confirmado.** `VideoStreamSender` constrói `H264RtpPacketizer(1200, 96, ...)`; captura no Ubuntu observou datagramas de 1.200 bytes. O packetizer fragmenta NALs grandes em FU-A. | Manter MTU de 1.200 bytes. Está abaixo da MTU Ethernet/Wi-Fi usual e evita fragmentação IP. | Nenhuma mudança. Adicionar ao diagnóstico Android o máximo de datagrama efetivamente enviado para detectar regressão. |
| `socket.send()` bloqueante | **Parcialmente verificado.** Vídeo tem uma thread própria `dji-video-primary`; telemetria usa `BoundedUdpSender` em outra thread. Porém `DatagramSocket.send()` é síncrono e pode bloquear essa thread de vídeo. O diagnóstico Android mede duração e buffer de envio efetivo, mas os máximos durante uma pausa ainda não foram capturados numa sessão que contenha a transição ativa→falha. | Manter vídeo e telemetria separados. Não enviar diagnóstico pesado no heartbeat. | SDK: expor em diagnóstico local e, se couber no health compacto, máximo/duração de `send`, profundidade/high-water da fila, drops, erros e último envio aceito. Correlacionar com um incidente Edge. |

## 2. Rede e isolamento de transporte

| Ponto | Estado e evidência | Decisão | Próxima verificação / dono |
|---|---|---|---|
| Baseline por USB tether | **Não executado.** É o experimento mais forte para separar Wi-Fi de sender/decoder. | Não mudar arquitetura antes deste controle. | Operador/SDK: repetir o mesmo teste com rede USB direta, mesma câmera e mesma janela de observação. Se a falha desaparecer, priorizar rede Wi-Fi; se persistir, priorizar sender Android. |
| Wi-Fi power saving / `WifiLock` | **Não encontrado** no transporte Android atual. | Adicionar um `WifiLock` de baixa latência/alto desempenho durante uma sessão de transporte ativa, adquirido e liberado pelo ciclo de vida do serviço. Não manter lock fora da sessão. | SDK: implementar e registrar aquisição/liberação/erro em NDJSON local. |
| Separação vídeo/telemetria | **Confirmado.** Video RTP usa `VideoStreamSender` e UDP :5600; telemetry/health usam `BoundedUdpSender` e UDP :5500; metadados usam :5501; clock :5502. Não há TCP nem multiplexação de vídeo com telemetria. | Manter as portas e threads separadas. | Nenhuma mudança de portas sem evidência. |
| Perda no receptor Ubuntu | **Kernel não está sobrecarregado**: `tcpdump` reportou zero pacotes descartados pelo kernel. Há eventos em que o Edge recebe metadado/health recente sem RTP. | Não aumentar filas do Edge: isto transformaria perda em latência. | O Edge agora expõe deltas agregados de RTP e jitterbuffer. PCAP, se necessário, é uma captura externa e explicitamente iniciada pelo operador; não é ativada nem gravada pelo transporte. |

## 3. Receptor PC: jitter, filas e decode

| Ponto | Estado e evidência | Decisão | Próxima verificação / dono |
|---|---|---|---|
| Jitter buffer | **Confirmado.** `udpsrc buffer-size=2097152` absorve bursts curtos no socket UDP antes do jitterbuffer; `rtpjitterbuffer latency=20 drop-on-latency=true do-lost=true` continua sendo o limite de latência. | Manter 20 ms enquanto o requisito for menor latência. O buffer do socket não retém frames nem aumenta a política do jitterbuffer; não aumentar a latência para mascarar pausas da origem/rede. | Só testar 50 ms como experimento controlado se houver reordenação comprovada e o custo de latência for aceitável. |
| Filas de vídeo | **Confirmado.** `appsink max-buffers=1 drop=true sync=false enable-last-sample=false`, seguido de handoff ROS do frame mais recente. | Manter. Não existe backlog de frames velhos nem retenção de uma amostra extra no Edge. | Monitorar `dropped_old_frames` e `superseded_frame_rate`; na sessão observada estavam em zero durante estado estável. |
| FFmpeg / OpenCV | **Não usados.** | Não adicionar. Seriam outro pipeline/fila sem benefício para o caminho ROS atual. | Nenhuma ação. |
| Decoder NVIDIA | **Confirmado em execução.** O dashboard mostra `nvidia` e a pipeline seleciona `nvh264dec`, com `avdec_h264` somente como fallback. | Manter detecção automática e fallback. | Continuar registrar backend e erro de pipeline no dashboard/evidência. |
| Logs de decoder | Não houve crash/erro GStreamer durante a observação. Há 50 incidentes “awaiting recoverable H.264/IDR”; isto é diagnóstico de stream incompleto, não crash. | Não usar watchdog de reinício por perda de frame. | PCAP + diagnóstico Android devem procurar a causa anterior ao IDR ausente: falta de datagramas, seq RTP e cadência de keyframe. |
| `rtp_sequence_gaps` | Existem muitos gaps cumulativos, mas não são convertidos automaticamente em perda de rede. | Manter como contador diagnóstico, não como decisão isolada. | Medir deltas por janela pareada Android/Edge e validar com PCAP. |

## 4. Timestamp e disciplina de clock

| Ponto | Estado e evidência | Decisão | Próxima verificação / dono |
|---|---|---|---|
| Clock RTP H.264 | **Confirmado.** O packetizer usa clock de 90 kHz e calcula RTP timestamp a partir de `SystemClock.elapsedRealtimeNanos()` relativo à origem da sessão. | Manter 90 kHz e timestamp monotônico. Não usar wall-clock. | SDK: teste unitário de incrementos e teste de stream real para verificar monotonicidade, wrap e cadência efetiva. |
| FPS e incremento esperado | A fonte observada no Edge é aproximadamente 30 FPS; nesse caso o incremento médio esperado é aproximadamente 3.000 ticks RTP por frame, mas pode variar com frame pacing real. | Não assumir FPS fixo no código. | Edge: PCAP/RTP analyzer deve calcular distribuição de delta RTP timestamp e comparar com metadados de Access Unit. |
| PTS/RTP causando congelamento | O `appsink` usa `sync=false`; portanto não espera o relógio do PC para apresentar um frame futuro. O jitter buffer ainda depende da continuidade RTP, mas não há sink sincronizado adicionando segundos de espera. | Manter `sync=false`, sem reescrever timestamps no Edge. | Se PCAP mostrar timestamps RTP regressivos ou saltos grandes, corrigir exclusivamente no packetizer Android. |
| Clock Android↔Edge | **Falhando intermitentemente** apesar de host configurado corretamente. A resposta Android não chega de forma confiável. O campo `clock_received` do receptor :5502 não é a métrica de ping Edge→Android; a métrica relevante é `successes/timeouts` do pinger. | Manter timestamp original Android/DJI intacto; timestamps Edge só medem latência e nunca substituem a origem. `clock ready` só fica verdadeiro enquanto a amostra for recente. | SDK: registrar ping recebido, destino/porta da resposta, pong enviado e exceção. Edge: preservar socket persistente e medir RTT/timeout. |

## Resolução: 3840×2160 no tablet versus 1280×720 no Edge

O dado **autoritativo para o transporte** é o SPS H.264 decodificado: o Edge
recebe 1280×720. O pipeline Edge não tem elemento de escala, logo ele não reduz
3840×2160 para 1280×720. O valor 3840×2160 exibido pelo aplicativo deve ser
tratado como perfil de câmera/gravação até o SDK provar que representa o mesmo
feed codificado enviado pelo `VideoFeeder`.

O SDK deve mostrar explicitamente, numa mesma tela/diagnóstico:

1. perfil DJI da câmera (resolução + FPS) após readback;
2. SPS da primeira Access Unit transmitida (largura/altura do H.264 real);
3. FPS de Access Units e FPS RTP enviados;
4. bitrate de RTP aceito pelo socket.

Sem esses quatro valores, “4K” não é uma afirmação utilizável para o transporte.

## Importação Android por ADB — sessão `10e89910-…`

Em 2026-09-18 foi copiado por ADB/USB, sem apagar o original do tablet:

```text
.runtime/android-import/10e89910-f1dd-4dff-aadb-e02488488338/transport.ndjson
SHA-256: 94ca9308e939cea7b6cb0dbd1f0cda4778dfee8b2c54ec94fc9470e0325724d0
```

O arquivo tem 11.389 linhas: 4.747 `transport_pulse` de aproximadamente 1 Hz
e 4.746 health records. A janela de pulsos coberta é 20:39:49–21:59:09 UTC em
2026-09-17 (79 min e 20 s).

### Resultado dessa janela

| Sinal Android | Resultado | Conclusão limitada, mas útil |
|---|---|---|
| Hub DJI Primary | `ingress_callbacks` ficou em 677.375 durante todos os 4.747 pulsos; a idade do último callback já era aproximadamente 97.518 s no primeiro pulso e aproximadamente 102.280 s no último. | Durante **toda esta janela**, nenhum frame Primary novo chegou ao hub DJI. |
| Listener do transport | `dji_callbacks` ficou no mesmo valor do hub, 677.375. | Não há indício de que o hub recebeu frames mas o listener do transport os bloqueou nesta janela. |
| Access Units e RTP Android | `access_units` ficou em 96.565; `rtp_accepted` em 1.228.397; sem novos bytes, sem erro RTP. | O packetizer/socket não são o primeiro ponto de falha nesta janela: não receberam vídeo novo para processar. |
| Serviço e NDJSON | `runtime_state=RUNNING`, foreground service vivo; writer vivo, fila ≤1, zero registros descartados e sem falha terminal. | O processo Android permaneceu saudável o bastante para diagnosticar; não foi uma queda do serviço nem do escritor. |
| Telemetria | `telemetry.accepted` avançou de 161.053 para 165.799, aproximadamente 1 datagrama/s; fila ≤1, drops zero. Há 107 erros acumulados, sem atribuição temporal neste arquivo. | A sessão Android e o caminho de telemetria continuaram ativos enquanto o Primary estava ausente. |
| Fan-out e `socket.send()` de vídeo | Os máximos por pulso de fan-out/offer/send ficaram zero porque não houve callbacks de vídeo no intervalo. | Este arquivo **não mede** duração de fan-out ou `send()` durante uma pausa ativa; não se deve concluir que estão bons ou ruins. |

### Interpretação correta

Esta importação classifica uma ausência prolongada de vídeo: **a cadeia parou
antes do transport listener e antes do packetizer**, no feed/hub DJI (ou no
binding que o alimenta). Ela não permite explicar uma pausa visual anterior se
essa pausa ocorreu antes de 20:39:49 UTC, porque a coleta começou quando os
contadores de vídeo já estavam congelados.

O próximo teste precisa iniciar uma sessão Android nova imediatamente antes de
ligar o transport e preservar o mesmo NDJSON até depois de uma pausa visível.
Somente assim os quatro estágios prometidos pelo SDK — hub, listener, AU e
socket — poderão ser comparados na transição ativa→falha.

## Próximo experimento que fecha a causa

1. No tablet, habilitar diagnóstico local de 1 Hz: callback DJI, chunks/AUs,
   RTP datagramas/bytes, fila, drops, duração máxima de `send`, erros, SPS e
   keyframes. Manter o heartbeat pequeno.
2. No Edge, iniciar uma PCAP silenciosa de 60–90 s durante uma pausa:
   `sudo timeout 90s tcpdump -ni wlp3s0 -s 0 -w /tmp/dji-primary.pcap 'udp port 5600 or udp port 5500 or udp port 5501 or udp port 5502'`.
3. Marcar hora aproximada de uma pausa visível e correlacionar PCAP, NDJSON
   Android e `.runtime/evidence` do Edge.
4. Repetir exatamente o mesmo cenário por USB tether.

Critério de decisão:

- Android continua packetizando/enviando e PCAP fica vazio: Wi-Fi/rota;
- Android para antes de `send`: callback, fila, parser ou ciclo de vida Android;
- PCAP contém RTP contínuo e Edge não recebe/decodifica: investigar Edge;
- RTP reaparece sem IDR e decoder espera: corrigir keyframe/IDR na origem.


## Pacote de revisão — onde está o problema e o que ainda falta provar

### A resposta curta

Ainda não existe uma única causa demonstrada para todas as travadas. Há **três
classes distintas de interrupção**, em pontos diferentes da cadeia. Misturá-las
leva a mudanças erradas no Edge:

| Classe | Evidência | Onde a causa pode estar | O que não explica |
|---|---|---|---|
| Feed DJI ausente | Na sessão \`10e89910-…\`, callbacks do hub, listener, AUs e RTP ficaram congelados por 79 min, enquanto o serviço e a telemetria continuaram vivos. | Feed DJI, binding Android, ligação Cendence/aeronave. | Não é um problema do UDP do Edge, porque nenhum vídeo chegou ao packetizer Android. |
| Android progride, Edge não vê RTP | Na sessão \`076257a5-…\`, houve 14 incidentes de progresso Android qualificado com lacuna de RTP no Edge e 13 de metadados Android recentes com RTP Edge obsoleto. | Wi-Fi/roteamento, entrega UDP após o tablet, ou medição ainda insuficiente para separar as duas. | Não prova que o Edge, NVIDIA ou ROS descartaram os datagramas. |
| Edge vê RTP, mas não decodifica imagem | 50 incidentes \`waiting_for_idr\`; RTP recente reapareceu sem uma AU recuperável para o H.264. | Perda de referências/H.264, ausência ou espaçamento excessivo de IDR, ou lacuna anterior que corrompeu o GOP. | Não significa que o decoder NVIDIA “caiu”; ele aguarda um quadro seguro para voltar a decodificar. |

O desligamento final registrado (\`AIRCRAFT_LINK\` seguido de
\`DJI_PRIMARY_BINDING_DETACHED\`) é um quarto evento, deliberadamente separado:
ele prova uma perda do lado DJI/Cendence naquele instante, mas não deve ser
usado para explicar todas as pausas anteriores.

### Fronteiras do sistema e grau de evidência

| Fronteira | O que foi observado | Confiança | Próprio responsável |
|---|---|---|---|
| Aeronave/Cendence → DJI SDK | Houve um desligamento de link seguido do detach do binding Primary. | Confirmado para o evento final; não generalizável para cada pausa. | Operador, cabo/controle/aeronave e SDK. |
| Feed DJI → hub Android | A sessão \`10e89910-…\` mostra 79 min sem callback novo no hub, com processo Android vivo. | Confirmado para essa janela. | SDK/DJI/feed/binding. |
| Hub → listener → AU → socket Android | Na janela ativa de \`076257a5-…\`, callbacks, AUs e RTP aceito avançaram sem drops ou resets reportados a 1 Hz. | Bom sinal, mas não elimina bloqueios subsegundo. | SDK/transport Android. |
| Socket Android → Wi-Fi/rota → NIC Edge | Há intervalos em que saúde/metadados Android avançaram e RTP não chegou ao Edge. | Suspeita forte; ainda não isolada por PCAP simultânea. | Rede/tablet/roteador/host. |
| UDP/RTP → GStreamer/NVIDIA → ROS | O Edge decodificou e publicou 1280×720 a cerca de 30 fps enquanto havia stream recuperável; também registrou espera por IDR quando não havia quadro seguro. | Confirmado como efeito; causa da perda anterior ainda aberta. | Edge, apenas se PCAP provar RTP contínuo e o pipeline falhar. |
| Clock Edge ↔ Android | Houve poucas respostas e muitos timeouts, apesar de algumas respostas válidas. Clock é independente do vídeo. | Confirmado como instável. | Contrato/serviço Android e rota de retorno UDP. |

### O que está comprovadamente saudável no Edge

- As portas e o contrato de entrada permanecem separados: telemetria/health em
  UDP \`5500\`, metadados em \`5501\`, clock em \`5502\` e Primary RTP/H.264 em
  \`5600\` (payload \`96\`, relógio RTP \`90 kHz\`).
- O caminho de vídeo não tem relay HTTP, FFmpeg, OpenCV, \`shmsink\`, preview
  gráfico nem escala no hot path. É um pipeline GStreamer por feed até um
  \`appsink\` de um quadro e então \`sensor_msgs/Image\` ROS.
- O decoder usado é NVIDIA quando o container o disponibiliza; CPU é fallback.
  O indicador correto de que está *ativo* não é só o nome do decoder, mas
  frames decodificados/publicados recentes. O dashboard separa \`ingress\`,
  \`decoder\` e entrega ROS precisamente por isso.
- \`appsink max-buffers=1 drop=true\` e o buffer ROS de um frame evitam backlog:
  quando o consumidor atrasa, o Edge prefere o quadro mais novo ao vídeo velho.
- A cópia final para uma imagem BGR em memória CPU existe porque
  \`sensor_msgs/Image\` é a interface ROS escolhida. Não há evidência de que ela
  seja a origem das pausas que também aparecem no preview GStreamer direto.

### O que não se deve alterar ainda

1. Não aumentar buffers para “resolver” a pausa: isso troca perda por latência
   crescente e dificulta a georreferência.
2. Não reintroduzir watchdog de reinício do decoder: depois de perda H.264, o
   comportamento seguro é aguardar o próximo IDR, não derrubar um Primary que
   pode se recuperar.
3. Não trocar portas, RTP, payload type, timestamp original ou criar relay
   extra. Não há evidência de que isso resolva a origem.
4. Não culpar NVIDIA/ROS apenas porque uma imagem congelou: a mesma pausa foi
   vista antes do ROS, e o Edge precisa primeiro ser comparado a uma PCAP.
5. Não concluir que o valor visual “4K” do tablet é o stream transportado. O
   SPS/caps e as imagens ROS observadas no Edge são 1280×720; o SDK precisa
   expor o perfil efetivamente entregue ao \`VideoFeeder\`.

### Experimentos na ordem certa

| Prioridade | Experimento controlado | Arquivos/resultados exigidos | Decisão que ele permite |
|---|---|---|---|
| P0 | Nova sessão: iniciar diagnóstico Android + evidência Edge + PCAP antes do vídeo; marcar uma pausa visível; parar somente depois da recuperação. | NDJSON Android da mesma sessão, \`.runtime/evidence/<sessão>/edge-*.ndjson\`, PCAP de 60–90 s e hora aproximada da pausa. | Localizar o primeiro estágio que parou: hub, listener, AU, socket, rede ou decoder. |
| P1 | Repetir P0 por USB tether, sem alterar encoder, portas ou Edge. | Mesmo conjunto de artefatos e mesma cena. | Se estabilizar, priorizar Wi-Fi/radio/bufferbloat; se não, priorizar SDK/encoder/sender/Edge. |
| P2 | Durante P0, registrar intervalos RTP, tamanho de datagramas e sequência RTP; no tablet registrar maior duração de \`send()\` e fila. | PCAP + máximos Android no intervalo da pausa. | Distinguir burst/perda de rede de bloqueio do sender. |
| P3 | Teste específico de clock com tablet no IP correto, após P0. | tentativas, respostas, RTT e timeouts durante 60 s. | Corrigir sincronização temporal sem confundi-la com disponibilidade de vídeo. |

### Lista objetiva para a revisão do SDK Android

Antes de alterar comportamento, o SDK deve responder com fonte de código e
medição de uma pausa real:

1. Qual é a resolução, FPS e perfil **do H.264 que sai do \`VideoFeeder\`**,
   confirmados por SPS e não por configuração de gravação/câmera?
2. Qual é a cadência efetiva de IDR/SPS/PPS no RTP? Há como solicitar IDR ou
   configurar a origem DJI? Não assumir \`MediaCodec\` se não é o encoder usado.
3. \`DjiDataHub\` mantém um único dono do callback Primary? Quais eventos fazem
   detach/rebind após perda de link Cendence/aeronave?
4. Durante uma pausa, os quatro contadores continuam a aumentar, nesta ordem:
   hub DJI, listener transport, Access Units, datagramas/bytes aceitos pelo
   socket? Registrar também duração máxima de fan-out e de \`send()\`.
5. O vídeo tem thread e socket independentes da telemetria? O socket tem MTU
   seguro (datagramas até 1200–1400 bytes) e a aplicação mantém \`WifiLock\`
   adequado enquanto o transport está ativo?
6. O respondedor de clock recebe \`ping\` na porta e responde para o IP/porta de
   origem corretos, preservando o identificador da requisição?

### Registro de evidências disponível para revisão

| Artefato | Local | Papel |
|---|---|---|
| Diagnóstico Android ativo | \`.runtime/android-import/076257a5-816f-46e3-b3f7-9fdb269b81a9/transport.ndjson\` | Mostra a janela ativa e a desconexão final. |
| Evidência Edge correspondente | \`.runtime/evidence/076257a5-816f-46e3-b3f7-9fdb269b81a9/edge-0.ndjson\` | Classifica os incidentes de RTP/IDR do mesmo identificador de sessão. |
| Diagnóstico Android de feed ausente | \`.runtime/android-import/10e89910-f1dd-4dff-aadb-e02488488338/transport.ndjson\` | Prova uma janela longa em que o hub DJI já não recebia Primary. |
| Código do Edge | \`src/dji_edge_transport/\` | Permite revisar o receptor UDP, pipeline GStreamer, estados e dashboard. |

### Conclusão operacional

O Edge já tem evidência suficiente para **não** receber mudanças especulativas
de arquitetura. A prioridade não é adicionar mais componentes: é executar P0
uma vez de forma correlacionada e seguir a primeira fronteira que parar. Se o
tablet ainda envia RTP e a PCAP o confirma, a investigação volta ao Edge; se o
tablet para antes disso, a correção pertence ao SDK/feed; se a PCAP fica vazia
enquanto o Android diz que enviou, a investigação é da rede/rota. O clock deve
ser corrigido em paralelo porque é requisito de sincronização espacial, mas ele
não é a explicação das congeladas de vídeo.


## Método de investigação causal e guia de continuação

### Método usado neste relatório

Este diagnóstico aplica uma versão enxuta de investigação forense a um sistema
distribuído: preservar artefatos, construir uma linha do tempo, manter hipóteses
concorrentes e executar um experimento que possa **refutar** cada hipótese. Isso
evita atribuir uma causa apenas porque dois contadores mudaram ao mesmo tempo.
A abordagem segue o princípio de preservação e limitação da evidência digital do
[NIST](https://www.nist.gov/publications/digital-investigation-techniques-nist-scientific-foundation-review)
e sua orientação de usar técnicas forenses também no troubleshooting de TI
([SP 800-86](https://csrc.nist.gov/pubs/sp/800/86/final)).

Regras:

1. Não comparar sessões distintas como se descrevessem o mesmo incidente.
2. Não converter contador RTP cumulativo diretamente em percentagem de perda.
   A taxa exige uma janela com sequência inicial/final e pacotes recebidos,
   conforme o modelo da [RFC 3550](https://datatracker.ietf.org/doc/html/rfc3550.html).
3. Preservar o timestamp monotônico da fonte; hora Edge serve para correlação e
   latência, nunca substitui a hora original.
4. Para cada pausa, obter ao mesmo tempo NDJSON Android, NDJSON Edge e PCAP.
5. Alterar apenas uma variável por experimento.

### Correção de causa e efeito

| Afirmação | Veredito | Razão |
|---|---|---|
| Drone/Cendence caiu repetidamente na sessão 076257a5 | **Refutado nesta sessão.** | Há um evento de conexão no início e um único disconnect no encerramento deliberado; há um único detach 0,504 s depois. |
| Detach final explica todas as travadas | **Refutado.** | As pausas RTP e espera por IDR ocorreram antes desse encerramento. |
| Hub DJI estava saudável em todas as pausas | **Ainda não provado.** | As amostras de 1 Hz avançaram na janela ativa, mas não eliminam bloqueios subsegundo. Outra sessão prova que ele pode ficar sem vídeo por longo período. |
| Sender Android falhou | **Não demonstrado.** | AUs e RTP aceito avançaram sem drops/reset/erro reportado; falta coincidir a duração máxima de send e a fila com uma pausa. |
| Wi-Fi é a causa | **Suspeita qualificada.** | Há progresso Android com RTP ausente no Edge, mas ainda não há PCAP simultânea nem teste USB A/B. |
| GStreamer/NVIDIA trava o vídeo | **Não demonstrado.** | Quando recebe H.264 recuperável, o Edge decodifica/publica; waiting_for_idr indica que falta um quadro seguro. O jitterbuffer trata atraso/perda dentro de sua janela, como documentado pelo [GStreamer](https://gstreamer.freedesktop.org/documentation/rtpmanager/rtpjitterbuffer.html). |
| ROS/RViz cria a primeira congelada | **Pouco provável.** | A pausa também ocorreu no preview GStreamer direto, antes da entrega ROS. |
| Clock intermitente para o vídeo | **Refutado como causa direta.** | Clock usa UDP 5502 em thread própria; Primary usa RTP 5600. Clock é requisito de sincronização espacial, não de decode H.264. |
| O vídeo transportado é 4K | **Não demonstrado.** | O tablet mostra 3840x2160, mas SPS/caps e a imagem ROS observada são 1280x720; o pipeline Edge não tem escala. |

### O que está fora da causa principal até nova evidência

- AIRCRAFT_LINK e DJI_PRIMARY_BINDING_DETACHED da sessão 076257a5 foram apenas
  a parada final deliberada.
- Portas, payload RTP 96, clock RTP 90 kHz, FU-A e MTU lógico de 1200 bytes são
  coerentes com o contrato observado.
- Aumentar buffers, inserir relay/FFmpeg/OpenCV/preview extra ou reiniciar
  automaticamente o decoder aumentaria estados e latência sem atacar a primeira
  fronteira que parou.
- NVIDIA não é causa sem evidência de RTP contínuo no host e falha posterior do
  decoder.
- Mapper/mapa não podem impedir que RTP chegue ao udpsrc: estão depois do decode.

### Matriz de investigação: local, código e evidência necessária

| Pergunta | Onde investigar | Arquivos/pontos exatos | Evidência que falta | Resultado que fecha a hipótese |
|---|---|---|---|---|
| O feed DJI entrega bytes ao app? | Antes do transport Android: aeronave, OcuSync, Cendence, USB e SDK. | SDK: Sample Code/app/src/main/java/com/dji/sdk/sample/djihub/DjiDataHub.java; criação de primaryVideoListener e chamadas addVideoDataListener/removeVideoDataListener. | callback ingress, idade, disponibilidade, AIRCRAFT_LINK, attach/detach e qualidade OcuSync na pausa. | Hub para primeiro: feed/binding/elo anterior, não Edge. |
| O fan-out do hub bloqueia? | SDK, logo após callback DJI. | DjiDataHub.java: listeners.dispatch e VideoDispatchMetrics.recordFanoutComplete; transport/TransportRuntime.java: onVideo chama primary.offer. | Máximo de fan-out e diferença entre callbacks do hub e do listener. | Hub continua e listener para/fan-out cresce: assinante síncrono/ownership SDK. |
| O parser/fila forma AUs? | SDK transport, antes do socket. | transport/video/EncodedVideoRtpPipeline.java, BoundedVideoIngestor, AnnexBParser, H264AccessUnitAssembler; construídos por transport/network/VideoStreamSender.java. | chunks, AUs, resets, SPS/PPS/IDR, fila e worker failure durante a pausa. | Hub/listener continuam e AU para: pipeline Android. |
| O sender bloqueia? | SDK, saída UDP. | VideoStreamSender.java: run, acceptPacket, send, openSocket e VideoSocketDiagnostics; transport/rtp/H264RtpPacketizer.java: packetize e rtpTimestamp. | datagramas/bytes aceitos, maior duração de send, erro, tamanho e sequência RTP. | AUs seguem mas RTP/socket para ou send cresce: sender/OS Android. |
| Wi-Fi/rota entrega ao laptop? | Entre tablet e Edge, fora do código. | NIC Ubuntu e AP/roteador; tcpdump no host. | PCAP UDP 5600 pareada com NDJSON Android, incluindo sequência/tamanho RTP. | Android envia e PCAP vazia: Wi-Fi/rota. PCAP contínua: seguir Edge. |
| Edge recebe e decodifica? | Edge, entrada RTP/decoder. | src/dji_edge_transport/src/primary_video_pipeline.cpp: udpsrc, rtpjitterbuffer, rtph264depay, decoder, appsink; src/dji_edge_transport/src/transport_node.cpp. | bytes/idade RTP, frames decoded, bus error e drops do jitterbuffer. | PCAP contínua mas decode falha: pipeline Edge. |
| ROS publica o frame novo? | Edge, após decode. | transport_node.cpp: delivery_loop, image_publisher, health_evaluator; include/dji_edge_transport/frame_handoff.hpp. | idade/count/FPS published, dropped_old_frames, superseded rate. | Decode segue e publish para: handoff/QoS ROS. |
| Clock ida/volta funciona? | SDK + Edge + rota de retorno. | SDK: transport/network/AndroidClockResponder.java; Edge: src/dji_edge_transport/src/clock_pinger.cpp e transport_node.cpp. | pings recebidos, pongs enviados, IP:porta, sequência, RTT e timeout. | Ping chega sem pong: resposta/rota. Ping não chega: destino/configuração/rede. |
| Dashboard mostra verdade? | Edge, somente observabilidade. | src/dji_edge_transport/src/dashboard_server.cpp e transport_node.cpp dashboard_state. | Comparar state/ingress_state/decoder_state/ros_delivery_state com status ROS. | Divergência: corrigir dashboard, sem mexer no vídeo. |

### Guia de execução da investigação restante

#### Fase A — preservar uma próxima pausa

**Donos:** operador, SDK e Edge.
**Não mudar:** encoder, perfil de vídeo, rede, portas, jitter buffer ou ROS.

1. Iniciar sessão Android nova e anotar o session_id.
2. Iniciar Edge e confirmar o mesmo session_id no dashboard/evidência.
3. Iniciar PCAP de 90 s no laptop antes de esperar a pausa, limitada a UDP 5600,
   5500, 5501 e 5502.
4. Anotar a hora de parede ao congelar e esperar recuperação, se houver.
5. Copiar NDJSON Android por ADB sem apagar o original; preservar PCAP e
   .runtime/evidence/<session_id>.

Sem esse trio de artefatos, não alterar código: só acumularíamos hipóteses.

#### Fase B — seguir a primeira fronteira parada

1. Hub para antes de AUs/RTP: SDK/feed/binding.
2. Hub continua, offer/AUs param: parser/fila Android.
3. AUs e RTP Android continuam, PCAP 5600 vazia: Wi-Fi/rota.
4. PCAP RTP contínua, Edge ingress não progride: host/NIC/container.
5. Ingress continua, decoded para e waiting_for_idr sobe: H.264/IDR/perda de
   referências; SDK deve provar a cadência real de keyframe.
6. Decoded continua e ROS publish para: handoff ROS.

#### Fase C — separar Wi-Fi de tablet

A única mudança é Wi-Fi por USB tether; encoder, app, portas e Edge ficam
iguais. A documentação Android diz que WifiLock de baixa latência só é efetivo
sob condições específicas de AP, tela e foreground; deve ser verificado no SDK,
não presumido. Ver [WifiManager](https://developer.android.com/reference/android/net/wifi/WifiManager).

- Estável em USB e instável em Wi-Fi: priorizar radio/agregação/energia/rota.
- Instável nos dois: priorizar SDK/feed/sender/Edge conforme Fase B.

### Limites de propriedade

| Domínio | Responsável por alterar | Papel deste repositório Edge |
|---|---|---|
| Câmera, feed DJI, OcuSync, bind/rebind, WifiLock, parser/packetizer e clock responder Android | SDK Android. | Ler e pedir evidência; não editar daqui. |
| Wi-Fi, USB tether, AP/roteador, canal/radio e NIC laptop | Operador/infraestrutura. | Capturar/medir; código não substitui diagnóstico de enlace. |
| UDP Edge, GStreamer, decoder, ROS, evidência e dashboard | Edge. | Corrigir apenas se evidência pareada mostrar a primeira quebra nesta fronteira. |
| Mapa, deteção e voo | Fora deste diagnóstico. | Não usar como explicação para perda de RTP. |
