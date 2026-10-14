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
	--env llm="$llm" \
	-- python TG.py
```

Requests use a model cascade. The fast classifier extracts intent and entities,
then routes routine work to Nano, standard work to Super, and serious reasoning
to Nemotron 3 Ultra. Override the defaults with `OYI_FAST_MODEL`,
`OYI_EVERYDAY_MODEL`, and `OYI_REASONING_MODEL` when exporting the sandbox
environment.

Mem0 uses Nebius for both memory extraction and embeddings. Persistent Mem0
data is stored under `/data/oyi-memory`, so mount an OCI block volume at
`/data` when deploying the container. The volume must survive container
recreation; the container filesystem alone is not durable.

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

The policy allows outbound HTTPS only to Telegram and Nebius. If the image or
runtime changes, update the two binary entries in `openshell-policy.yaml` using
`readlink -f` inside the sandbox image.
