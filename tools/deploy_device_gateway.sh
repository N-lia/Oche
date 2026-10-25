#!/usr/bin/env bash
# Deploy the OYI device gateway to the live webapp host and print the values
# the phone needs.
#
#   bash tools/deploy_device_gateway.sh [deploy-dir]
#
# Run it on the machine that serves the OYI webapp (the Oracle Cloud VM).
#
# It works out where the running deployment lives (from the Docker compose
# labels), pulls android-device-app there, makes sure the device environment is
# set, rebuilds the webapp image, restarts it behind Caddy and prints the
# gateway URL plus the pairing token for the phone.
#
# Environment overrides:
#   OYI_BRANCH         branch to deploy (default android-device-app)
#   OYI_DEPLOY_DIR     deployment directory (default: auto-detected)
#   OYI_COMPOSE_FILE   compose file (default compose.webapp.yaml)
#   OYI_ENV_FILE       env file (default <deploy-dir>/.env)
set -euo pipefail

BRANCH="${OYI_BRANCH:-android-device-app}"
COMPOSE_FILE="${OYI_COMPOSE_FILE:-compose.webapp.yaml}"

say() { printf '\n== %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------
# 1. Find the deployment directory
# --------------------------------------------------------------------------
detect_deploy_dir() {
  local container
  container=$(docker ps --format '{{.Names}}' | grep -E 'webapp' | head -1 || true)
  [ -n "$container" ] || return 1
  docker inspect "$container" \
    --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null
}

detect_project() {
  local container
  container=$(docker ps --format '{{.Names}}' | grep -E 'webapp' | head -1 || true)
  [ -n "$container" ] || return 1
  docker inspect "$container" \
    --format '{{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null
}

DEPLOY_DIR="${1:-${OYI_DEPLOY_DIR:-}}"
if [ -z "$DEPLOY_DIR" ]; then
  DEPLOY_DIR=$(detect_deploy_dir || true)
fi
[ -n "$DEPLOY_DIR" ] || DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PROJECT="${OYI_COMPOSE_PROJECT:-$(detect_project || true)}"
[ -n "$PROJECT" ] || PROJECT="oyi-live"

ENV_FILE="${OYI_ENV_FILE:-$DEPLOY_DIR/.env}"

say "Deployment directory: $DEPLOY_DIR"
[ -d "$DEPLOY_DIR" ] || die "$DEPLOY_DIR does not exist.
Find it with:
  docker inspect \$(docker ps --format '{{.Names}}' | grep webapp | head -1) \\
    --format '{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}'"

cd "$DEPLOY_DIR"

# --------------------------------------------------------------------------
# 2. Get the branch
# --------------------------------------------------------------------------
if [ -d .git ]; then
  say "Pull $BRANCH"
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
else
  die "$DEPLOY_DIR is not a git checkout, so it cannot be updated in place.
Get the code first, then run this script again:

  git clone --branch $BRANCH https://github.com/N-lia/Oche.git $DEPLOY_DIR

(or copy the branch over the existing files:
  git clone --branch $BRANCH https://github.com/N-lia/Oche.git /tmp/oyi-src
  rsync -a --exclude .git --exclude .env --exclude data /tmp/oyi-src/ $DEPLOY_DIR/)"
fi

# --------------------------------------------------------------------------
# 3. Device environment
# --------------------------------------------------------------------------
say "Make sure the device environment is set"
[ -f "$ENV_FILE" ] || die "no $ENV_FILE"

set_env() {  # set_env <KEY> <value>
  local key="$1" value="$2"
  if grep -qE "^[[:space:]]*$key[[:space:]]*=" "$ENV_FILE"; then
    sed -i -E "s|^[[:space:]]*$key[[:space:]]*=.*$|$key = $value|" "$ENV_FILE"
  else
    printf '%s = %s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# A missing key must not abort the script under "set -e".
read_env() {  # read_env <KEY> -> value ("" when absent)
  grep -E "^[[:space:]]*$1[[:space:]]*=" "$ENV_FILE" 2>/dev/null \
    | cut -d= -f2- | tr -d ' ' || true
}

DOMAIN=$(read_env WEBAPP_DOMAIN)
[ -n "$DOMAIN" ] || die "WEBAPP_DOMAIN is missing from $ENV_FILE"

TOKEN=$(read_env OYI_DEVICE_TOKEN)
if [ -z "$TOKEN" ]; then
  TOKEN=$(openssl rand -hex 32)
  echo "generated a new OYI_DEVICE_TOKEN"
fi
API_TOKEN=$(read_env OYI_DEVICE_API_TOKEN)
if [ -z "$API_TOKEN" ]; then
  API_TOKEN="$TOKEN"
fi
grep -qE "^[[:space:]]*NEBIUS_API_KEY[[:space:]]*=.+" "$ENV_FILE" || \
  die "NEBIUS_API_KEY is missing from $ENV_FILE (the vision planner needs it)"

set_env OYI_DEVICE_TOKEN "$TOKEN"
set_env OYI_DEVICE_API_TOKEN "$API_TOKEN"
set_env OYI_DEVICE_VISION_MODEL "nvidia/Nemotron-3.5-Super-VL"
# The agent runs inside the webapp, so its control_phone tool needs to know
# where the gateway lives as well.
set_env OYI_DEVICE_GATEWAY_URL "https://${DOMAIN}"

# --------------------------------------------------------------------------
# 4. Rebuild
# --------------------------------------------------------------------------
say "Rebuild and restart the webapp (project: $PROJECT)"
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d --build webapp
docker compose -p "$PROJECT" -f "$COMPOSE_FILE" up -d caddy

say "Wait for the device API"
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null -H "Authorization: Bearer $API_TOKEN" \
      "https://${DOMAIN}/api/device/health"; then
    break
  fi
  sleep 3
done
curl -s -H "Authorization: Bearer $API_TOKEN" "https://${DOMAIN}/api/device/health" || true
echo

# --------------------------------------------------------------------------
# 5. What the phone and the bot need
# --------------------------------------------------------------------------
say "Next steps"
cat <<EOF
Point the Telegram bot at the gateway (openshell sandbox create/update):

  --env OYI_DEVICE_GATEWAY_URL="https://${DOMAIN}"
  --env OYI_DEVICE_API_TOKEN="$API_TOKEN"

Then enter this in the OYI Android app:

  Gateway URL:   https://${DOMAIN}
  Pairing token: $TOKEN
EOF
