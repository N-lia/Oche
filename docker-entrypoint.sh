#!/bin/sh
set -eu

required_vars="TELEGRAM_BOT_TOKEN My_Telegram_ID NEBIUS_API_KEY"
for name in $required_vars; do
    eval "value=\${$name-}"
    if [ -z "$value" ]; then
        echo "Missing required environment variable: $name" >&2
        exit 64
    fi
done

exec "$@"
