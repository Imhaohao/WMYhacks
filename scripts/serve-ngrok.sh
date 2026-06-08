#!/usr/bin/env bash
#
# serve-ngrok.sh — put the Gotchu site + API on a PERMANENT ngrok URL.
#
# Unlike the cloudflared quick tunnel (random URL each run), ngrok's free static
# domain gives you ONE stable hostname forever. Register the two Google OAuth
# redirect URIs once and never touch the console again.
#
# One-time setup:
#   1. brew install ngrok            (or it's already installed)
#   2. Sign up free:   https://dashboard.ngrok.com/signup
#   3. Add authtoken:  ngrok config add-authtoken <YOUR_TOKEN>
#   4. Claim a free static domain at https://dashboard.ngrok.com/domains
#   5. Add these to your Google OAuth client (console.cloud.google.com/apis/credentials):
#        https://<YOUR_DOMAIN>/api/calendar/google/callback
#        https://<YOUR_DOMAIN>/api/gmail/google/callback
#        https://<YOUR_DOMAIN>/api/onboarding/google/calendar/callback
#        https://<YOUR_DOMAIN>/api/onboarding/google/gmail/callback
#
# Usage:
#   NGROK_DOMAIN=your-name-1234.ngrok-free.app ./scripts/serve-ngrok.sh
#   # or save it once:
#   echo 'your-name-1234.ngrok-free.app' > scripts/.ngrok-domain
#   ./scripts/serve-ngrok.sh
#
# Stop with Ctrl-C (tears down both the API and the tunnel).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT/server"
WEB_DIR="$ROOT/web"
PORT="${PORT:-8787}"
DOMAIN_FILE="$ROOT/scripts/.ngrok-domain"

# Resolve the static domain: env var wins, else the saved file.
DOMAIN="${NGROK_DOMAIN:-}"
if [[ -z "$DOMAIN" && -f "$DOMAIN_FILE" ]]; then
  DOMAIN="$(tr -d '[:space:]' < "$DOMAIN_FILE")"
fi
if [[ -z "$DOMAIN" ]]; then
  echo "✗ No ngrok domain set." >&2
  echo "  Pass it:   NGROK_DOMAIN=your-name.ngrok-free.app ./scripts/serve-ngrok.sh" >&2
  echo "  Or save:   echo 'your-name.ngrok-free.app' > scripts/.ngrok-domain" >&2
  exit 1
fi
# Strip any accidental https:// prefix.
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN%/}"

PUBLIC_URL="https://${DOMAIN}"
NGROK_PID=""
API_PID=""

cleanup() {
  echo
  echo "› Shutting down…"
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "$NGROK_PID" ]] && kill "$NGROK_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

command -v ngrok >/dev/null 2>&1 || {
  echo "✗ ngrok not found. Install with: brew install ngrok" >&2
  exit 1
}

# Verify an authtoken is configured (ngrok stores it in its config file).
if ! ngrok config check >/dev/null 2>&1; then
  echo "✗ ngrok isn't configured. Run: ngrok config add-authtoken <YOUR_TOKEN>" >&2
  echo "  Get your token at https://dashboard.ngrok.com/get-started/your-authtoken" >&2
  exit 1
fi

# Free the API port if a previous run / dev server is holding it.
if lsof -ti "tcp:${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "› Port ${PORT} is busy — stopping the stale process holding it…"
  lsof -ti "tcp:${PORT}" -sTCP:LISTEN | xargs kill 2>/dev/null || true
  sleep 1
  if lsof -ti "tcp:${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    lsof -ti "tcp:${PORT}" -sTCP:LISTEN | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
fi

echo "› Building frontend (web/dist)…"
( cd "$WEB_DIR" && [[ -d node_modules ]] || npm install )
( cd "$WEB_DIR" && npm run build )

echo "› Opening ngrok tunnel on ${PUBLIC_URL} → localhost:${PORT}…"
ngrok http "$PORT" --domain "$DOMAIN" --log stdout >/tmp/gotchu-ngrok.log 2>&1 &
NGROK_PID=$!
sleep 3

cat <<EOF

────────────────────────────────────────────────────────────────────
✓ Permanent site:  ${PUBLIC_URL}
  Setup wizard:    ${PUBLIC_URL}/setup

  Google OAuth redirect URIs (register ONCE, then never again):
    ${PUBLIC_URL}/api/calendar/google/callback
    ${PUBLIC_URL}/api/gmail/google/callback
    ${PUBLIC_URL}/api/onboarding/google/calendar/callback
    ${PUBLIC_URL}/api/onboarding/google/gmail/callback
────────────────────────────────────────────────────────────────────

EOF

echo "› Starting API + site on :${PORT} (Ctrl-C to stop)…"
cd "$SERVER_DIR"
PUBLIC_BASE_URL="$PUBLIC_URL" PORT="$PORT" uv run python onboarding_api.py &
API_PID=$!

wait "$API_PID"
