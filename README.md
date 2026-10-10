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
