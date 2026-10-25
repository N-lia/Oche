#!/usr/bin/env bash
# Deploy the OYI device gateway to the live webapp host and print the values
# the phone needs.
#
#   bash tools/deploy_device_gateway.sh
#
# Run it on the machine that serves the OYI webapp (the Oracle Cloud VM).
# It pulls android-device-app, makes sure the device environment is set,
# rebuilds the webapp image and restarts it behind Caddy.
set -euo pipefail

BRANCH="${OYI_BRANCH:-android-device-app}"
PROJECT="${OYI_COMPOSE_PROJECT:-oyi-live}"
COMPOSE_FILE="${OYI_COMPOSE_FILE:-compose.webapp.yaml}"
REPO="${OYI_REPO:-/home/mario/OYI}"
ENV_FILE="${OYI_ENV_FILE:-$REPO/.env}"

cd "$REPO"

say() { printf '\n== %s\n' "$*"; }

say "Fetch $BRANCH"
if git rev-parse --git-dir >/dev/null 2>&1; then
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" https://github.com/N-lia/Oche.git "$REPO"
fi

say "Make sure the device environment is set"
[ -f "$ENV_FILE" ] || { echo "no $ENV_FILE"; exit 1; }

set_env() {  # set_env <KEY> <value>
  local key="$1" value="$2"
  if grep -qE "^[[:space:]]*$key[[:space:]]*=" "$ENV_FILE"; then
    # Replace the value, keeping the file readable.
    sed -i -E "s|^[[:space:]]*$key[[:space:]]*=.*$|$key = $value|" "$ENV_FILE"
  else
    printf '%s = %s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

TOKEN=$(grep -E "^[[:space:]]*OYI_DEVICE_TOKEN[[:space:]]*=" "$ENV_FILE" | cut -d= -f2- | tr -d ' ')
if [ -z "$TOKEN" ]; then
  TOKEN=$(openssl rand -hex 32)
  echo "generated a new OYI_DEVICE_TOKEN"
fi
API_TOKEN=$(grep -E "^[[:space:]]*OYI_DEVICE_API_TOKEN[[:space:]]*=" "$ENV_FILE" | cut -d= -f2- | tr -d ' ')
if [ -z "$API_TOKEN" ]; then
  API_TOKEN="$TOKEN"
fi
grep -qE "^[[:space:]]*NEBIUS_API_KEY[[:space:]]*=.+" "$ENV_FILE" || {
  echo "NEBIUS_API_KEY is missing from $ENV_FILE (the vision planner needs it)"
  exit 1
}

set_env OYI_DEVICE_TOKEN "$TOKEN"
set_env OYI_DEVICE_API_TOKEN "$API_TOKEN"
set_env OYI_DEVICE_VISION_MODEL "nvidia/Nemotron-3.5-Super-VL"

say "Rebuild and restart the webapp"
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --build webapp
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d caddy

say "Wait for the device API"
DOMAIN=$(grep -E "^[[:space:]]*WEBAPP_DOMAIN[[:space:]]*=" "$ENV_FILE" | cut -d= -f2- | tr -d ' ')
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null -H "Authorization: Bearer $API_TOKEN" \
      "https://${DOMAIN}/api/device/health"; then
    break
  fi
  sleep 3
done
curl -s -H "Authorization: Bearer $API_TOKEN" "https://${DOMAIN}/api/device/health"
echo

say "Point the Telegram bot at the gateway"
cat <<EOF
Add these to the bot's sandbox (openshell sandbox create / update):

  --env OYI_DEVICE_GATEWAY_URL="https://${DOMAIN}"
  --env OYI_DEVICE_API_TOKEN="$API_TOKEN"

Then enter this in the OYI Android app:

  Gateway URL: https://${DOMAIN}
  Pairing token: $TOKEN
EOF
