## Run with OpenShell

This project runs the Telegram bot in an OpenShell sandbox. The bot keeps its
existing polling entry point (`TG.py`); OpenShell supplies process isolation
and outbound network policy around it.

Build the image with Docker or Podman:

```sh
docker build -t oyi-telegram:local .
```

Make sure an OpenShell gateway is running and selected, then create the
sandbox. Do not put credentials in the image or commit a `.env` file.

```sh
openshell sandbox create \
	--name oyi-telegram \
	--from oyi-telegram:local \
	--policy openshell-policy.yaml \
	--detach \
	--restart-policy on-failure \
	--env TELEGRAM_BOT_TOKEN="$TELEGRAM_BOT_TOKEN" \
	--env My_Telegram_ID="$My_Telegram_ID" \
	--env NEBIUS_API_KEY="$NEBIUS_API_KEY" \
	--env NEBIUS_SANDBOX_URL="$NEBIUS_SANDBOX_URL" \
	--env PARAKEET_GRPC_TARGET="$PARAKEET_GRPC_TARGET" \
	--env CHATTERBOX_MODAL_URL="$CHATTERBOX_MODAL_URL" \
	--env TAVILY_API_KEY="$TAVILY_API_KEY" \
	-- python TG.py
```

The image entrypoint validates the required Telegram and Nebius variables
without printing their values. Mem0 stores its Qdrant data and SQLite history
under `/data/oyi-memory`.

## Speech-to-text service (Oracle Cloud VM)

Parakeet-EOU-120M ASR does not run on Modal. It runs on the Oracle Cloud VM as
a gRPC service (`asr_server.py`) and the bot reaches it over the network.
Chatterbox-Turbo TTS still runs on Modal and is called over HTTPS by `voice.py`.

Start the ASR service on the VM:

```sh
docker compose up -d --build parakeet-asr
docker compose logs -f parakeet-asr
```

Then point the bot and the sandbox policy at the VM host address:

```sh
export PARAKEET_GRPC_TARGET="10.0.0.10:50051"   # or host.docker.internal:50051
```

Set the same host and port in `openshell-policy.yaml` under `parakeet_asr` so
the sandboxed bot may open the gRPC connection. Optional variables:

- `PARAKEET_GRPC_TIMEOUT` per-request timeout in seconds (default `45`).
- `PARAKEET_GRPC_TLS` set truthy only when the service is behind TLS.
- `PARAKEET_MODEL_DIR` (server) model directory; `PARAKEET_GRPC_BIND` (server)
  bind address, default `0.0.0.0:50051`.

### Cache-aware streaming

`StreamingSession` keeps the Conformer attention/convolution caches and the
RNNT decoder state across 160 ms chunks, so each chunk is encoded exactly once
per turn. Partial transcripts no longer re-decode the whole utterance (O(n^2)
→ O(n)); the final incremental transcript is byte-identical to a one-shot
decode.

### Smart Turn v3 (semantic endpointing)

The ASR service also hosts Pipecat's **Smart Turn v3** model (`smart_turn.py`,
`ScoreTurn` RPC). An acoustic VAD can only hear a pause; Smart Turn scores
whether the last 8 s of waveform sound *finished*.

- On `speech_end`, `voice_session.py` scores the turn. A probability ≥
  `SMART_TURN_THRESHOLD` (default `0.5`) dispatches immediately; below it the
  turn is **held open** for `SMART_TURN_GRACE_MS` (default `1800`). If the user
  keeps talking, the same turn and ASR stream resume — no fragment is sent to
  the agent.
- When the service reports Smart Turn ready, the WebApp uses a 500 ms VAD
  silence window; without it the client falls back to 750 ms.
- `SMART_TURN_ENABLED=0` disables the client side; `SMART_TURN_MODEL_DIR=""` on
  the server disables loading; `SMART_TURN_AUTO_DOWNLOAD=1` (default) fetches
  `smart-turn-v3.2-cpu.onnx` into `/models/smart-turn` on first start.

Test the service directly:

```sh
.venv/bin/python test_parakeet_eou.py normal.wav --target "$PARAKEET_GRPC_TARGET"
.venv/bin/python test_asr_cache_stream.py   # cache-aware equivalence
.venv/bin/python test_smart_turn.py          # feature + model checks
```

## Live Chat WebApp (`/livechat`)

The WebApp is a real-time conversation surface that streams microphone audio to
the ASR service over the `/ws/voice` WebSocket and keeps the exchange alive with
**backchannels** ("mm-hmm", "right", "go on") while you talk and **thinking
fillers** ("hmm…", "let me see…") right after you stop. It also takes typed
messages and shows a live **agent-activity** stream (thinking, tool use).

- Open it from Telegram with `/livechat` (Telegram command names allow only
  `a-z0-9_`, so a typed `/live-chat` is also accepted but `/livechat` is the
  canonical, menu-registered command) or the 🎧 Live Chat menu button.
- `webapp_server.py` opens a `StreamTranscribe` gRPC stream per turn, emits
  partial transcripts, and runs the backchannel policy in `backchannel.py`.
- `voice_session.py` holds the turn state machine: audio/text → ASR → backchannels
  → agent → TTS. It is web-framework free so the turn-taking logic is unit-tested.
- **Turn-taking is local and acoustic.** `webapp/turn-taking.js` runs a
  BargeKit-style state machine on the microphone level (`armed → user_speaking →
  cooldown`, `agent_speaking → barge_pending → interrupted`). The client owns
  turn boundaries: it sends `speech_start`/`speech_end` (with a preroll) and only
  streams audio inside a turn, so noise and speaker echo can never open one.
- **Continuers are not requests.** `turn_policy.py` classifies the final
  transcript: "hmm", "ok", "right", "mm-hmm" are acknowledgements and never reach
  the agent. A bare "ok" only counts as an answer when the previous reply ended
  with a question. Genuine requests go to the agent as usual.
- **Barge-in ducks, then decides.** Talking over the assistant ducks its audio
  immediately but does not cancel the reply. If the transcript is a continuer,
  playback resumes; if it is a real turn, the reply is cancelled and the new turn
  runs. Sustained speech also cancels locally after ~900 ms, and the settings
  sheet offers half-duplex mode (mic muted while the agent speaks).
- **Interruptible turns.** Every turn carries a monotonic `turn_id`. A barge-in
  tears down the previous ASR stream and opens a new turn seeded with the
  preroll, so the first word is not clipped. Stragglers from the abandoned turn
  are dropped by `turn_id`.
- **Text turns** send `{"type": "text", …}`; the session streams
  `status`/`tool`/`bot_text_delta` events and optionally speaks the reply.
- Backchannel clips are short phrases pre-synthesized in OYI's voice with the
  soft `backchannel` mood and cached under `BACKCHANNEL_CACHE_DIR`
  (default `data/backchannels`) so playback never waits on Modal.
- Redeploy Chatterbox once so the new preset exists:
  `modal deploy modal_chatterbox.py`.

Run the WebApp locally:

```sh
pip install -r requirements-webapp.txt
python webapp_server.py          # http://0.0.0.0:8000
```

Warm the clip library explicitly (optional):

```sh
curl -X POST http://localhost:8000/api/backchannels/prewarm
curl http://localhost:8000/api/backchannels
curl http://localhost:8000/api/health      # ASR/TTS status for the header
```

### Deploy (Oracle Cloud VM)

Telegram WebApps require a public HTTPS URL. `compose.webapp.yaml` runs the
WebApp plus Caddy (automatic Let's Encrypt TLS) next to the ASR service:

```sh
# on the VM, in a directory holding the repo files + .env
docker compose -f compose.webapp.yaml up -d --build
docker compose -f compose.webapp.yaml logs -f caddy   # watch cert issuance
```

`.env` needs `WEBAPP_DOMAIN` (e.g. `150-230-152-243.sslip.io`, which resolves to
the embedded IP — no domain purchase needed) and inbound TCP **80 + 443** open in
the OCI VCN security list. Point the bot at it with
`TELEGRAM_WEBAPP_URL=https://<WEBAPP_DOMAIN>`.

Diagram generation goes through the Nebius token sandbox. Set
`NEBIUS_SANDBOX_URL` to the endpoint that accepts
`{"mermaid": "...", "format": "svg"}` and returns `{"svg": "..."}` (a JSON
`url` field also works); `NEBIUS_API_KEY`, when set, is sent as a Bearer token.
Without the URL `render_diagram` returns a clear error instead of inventing an
image. The model then emits the validated Mermaid source and the WebApp renders
it live with pele; spoken replies strip the diagram source.

Backchannel pacing is tunable via `BackchannelController` (`min_gap`,
`first_after`, `long_turn`, `pause`, `max_per_turn`) and the energy VAD threshold
`BACKCHANNEL_VAD_RMS`. Test the policy and the interruption state machine with:

```sh
.venv/bin/python test_backchannel.py
.venv/bin/python test_turn_policy.py
.venv/bin/python test_voice_interrupt.py
.venv/bin/python test_live_chat_text.py
node test_turn_taking.js
```

Requests use a model cascade. The fast classifier extracts intent and entities,
then routes routine work to Nano, standard work to Super, and serious reasoning
to Nemotron 3 Ultra. Override the defaults with `OYI_BASE_MODEL`,
`OYI_REASONING_MODEL`, and `OYI_INTENSE_MODEL` when exporting the sandbox
environment.

External requests pass through a local privacy scrubber before reaching Tavily or
Nebius. Set `OYI_PII_PROJECT_NAMES` to a comma-separated list of sensitive project
names. Values are replaced with per-request aliases; use `prepare_mail()` from
`privacy.py` only at a trusted local mail-delivery boundary to restore the original
values. The scrubber never sends its alias vault to external services.

## Module boundaries

- `agent_service.py` owns model construction, routing, memory context, privacy, and streaming.
- `main.py` is a compatibility facade for the agent service API.
- `telegram_support.py` owns Telegram settings, rate limiting, and message formatting helpers.
- `TG.py` owns Telegram handlers, document transport, and application lifecycle.
- `asr_server.py` hosts the Parakeet-EOU gRPC ASR service on the VM; `asr_client.py` is its client used by `TG.py` and `webapp_server.py`.
- `memory.py`, `privacy.py`, `router.py`, and `tool.py` provide reusable domain services.

Mem0 uses Nebius for memory extraction and embeddings, and local Qdrant for
the vector store. Qdrant runs in-process; no separate Qdrant service is
required. For Oracle Cloud, attach an OCI Block Volume and mount it at
`/data` so memories survive VM or container recreation.

For OCI Container Instances, mount persistent block storage at `/data` and
inject the Telegram and Nebius values through OCI Vault or Container Instance
secrets. For OpenShell on an Oracle Cloud VM, build the image on the VM,
attach the block volume, and launch it with the OpenShell command above.

The values above must already be exported in the shell. The `--env` form makes
them available to the sandbox process; OpenShell warns when an environment
variable looks like a credential. For stronger credential isolation, replace
the direct `--env` values with OpenShell provider profiles and bindings.

Monitor or stop the bot with:

```sh
openshell logs oyi-telegram --tail
openshell sandbox get oyi-telegram
openshell sandbox stop oyi-telegram
```

The policy allows outbound HTTPS only to Telegram, Nebius, and Modal, plus the
gRPC connection to the local `parakeet_asr` service. If the image or runtime
changes, update the binary entries in `openshell-policy.yaml` using
`readlink -f` inside the sandbox image.
