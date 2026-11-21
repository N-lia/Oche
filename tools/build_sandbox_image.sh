#!/usr/bin/env bash
# Build and smoke-test the sandbox image used by OYI's agent shell.
#
# The image ships Python 3 (deepagents file tools) and Node 20 + Pele
# (Mermaid -> SVG). Build it once per Nebius project, then OYI can use
# OYI_SANDBOX_IMAGE=oyi-sandbox:latest.
#
# Usage:
#   tools/build_sandbox_image.sh
#   OYI_SANDBOX_IMAGE=oyi-sandbox:my-tag tools/build_sandbox_image.sh
#
# Credentials:
#   NEBIUS_API_KEY and NEBIUS_PROJECT_ID, from the environment or .env.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TAG="${OYI_SANDBOX_IMAGE:-oyi-sandbox:latest}"
TIMEOUT="${OYI_SANDBOX_BUILD_TIMEOUT:-900}"

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

read_env() {
  local key=$1
  [ -f "$ROOT/.env" ] || return 0
  sed -nE "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//p" "$ROOT/.env" \
    | head -1 | tr -d '"' | tr -d "'" | tr -d '[:space:]'
}

if [ -z "${NEBIUS_API_KEY:-}" ]; then
  NEBIUS_API_KEY=$(read_env NEBIUS_API_KEY)
fi
if [ -z "${NEBIUS_PROJECT_ID:-}" ]; then
  NEBIUS_PROJECT_ID=$(read_env NEBIUS_PROJECT_ID)
fi
export NEBIUS_API_KEY NEBIUS_PROJECT_ID
# The CLI reads its profile variable; the SDK reads NEBIUS_PROJECT_ID.
export NEBIUS_AI_PROJECT="${NEBIUS_AI_PROJECT:-${NEBIUS_PROJECT_ID:-}}"

[ -n "${NEBIUS_API_KEY:-}" ] || die "NEBIUS_API_KEY is not set (environment or .env)"
[ -n "${NEBIUS_PROJECT_ID:-}" ] || die "NEBIUS_PROJECT_ID is not set (environment or .env)"

if ! command -v contree >/dev/null 2>&1; then
  command -v uv >/dev/null 2>&1 || die "contree is not installed and uv is unavailable"
  say "Installing the Contree CLI"
  uv tool install contree-cli==0.9.4
  export PATH="$HOME/.local/bin:$PATH"
fi

# The profile may be stale if the key changed; this is a no-op when it is valid.
say "Checking Sandboxes access"
contree auth -y >/dev/null
contree auth ls 2>/dev/null | sed -n '1,6p' || true

say "Building $TAG"
contree build "$ROOT/sandbox_image" --tag "$TAG" --timeout "$TIMEOUT"

say "Smoke-testing $TAG"
contree run -D --use "$TAG" -- sh -c '
  set -e
  node -v
  python3 -V
  printf "flowchart LR\n  A[Mermaid] --> B[SVG]\n" | render-mermaid -o /tmp/smoke.svg --png /tmp/smoke.png
  test -s /tmp/smoke.svg
  grep -q "<svg" /tmp/smoke.svg
  test "$(od -An -tx1 -N8 /tmp/smoke.png | tr -d "[:space:]")" = "89504e470d0a1a0a"
  test "$(wc -c < /tmp/smoke.png)" -gt 2000
  fc-match sans-serif | grep -qi dejavu
  echo "sandbox render: OK (SVG + PNG)"
' || die "the image was built but the smoke test failed"

say "Ready"
echo "  image: $TAG"
echo "  set:   OYI_SANDBOX_IMAGE=$TAG"
echo "  the deploy script writes this automatically when Sandboxes are enabled"
