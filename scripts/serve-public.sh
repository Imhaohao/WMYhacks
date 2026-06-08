#!/usr/bin/env bash
#
# serve-public.sh — put the Gotchu onboarding site + API on a public HTTPS URL.
#
# What it does:
#   1. Builds the React frontend (web/dist).
#   2. Opens a Cloudflare quick tunnel to the local API (port 8787) and grabs
#      the public https://<random>.trycloudflare.com URL.
#   3. Starts the FastAPI server with PUBLIC_BASE_URL set to that URL, so it
#      serves the built site AND its own /api/* on one origin — and so Google
#      OAuth callbacks come back to the public host.
#   4. Prints the two redirect URIs you must add to your Google OAuth client.
#
# One origin = one tunnel = OAuth "just works" (same-origin callback).
#
# Usage:
#   ./scripts/serve-public.sh
#
# Stop with Ctrl-C (both the tunnel and the API are torn down).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT/server"
WEB_DIR="$ROOT/web"
PORT="${PORT:-8787}"

CF_LOG="$(mktemp -t gotchu-cloudflared.XXXXXX)"
CF_PID=""
API_PID=""

cleanup() {
  echo
  echo "› Shutting down…"
  [[ -n "$API_PID" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "$CF_PID" ]] && kill "$CF_PID" 2>/dev/null || true
  rm -f "$CF_LOG" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

command -v cloudflared >/dev/null 2>&1 || {
  echo "✗ cloudflared not found. Install with: brew install cloudflared" >&2
  exit 1
}

# Free the API port if a previous run (or a stray dev server) is still holding
# it — otherwise uvicorn fails with "address already in use".
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

echo "› Opening Cloudflare tunnel to http://localhost:${PORT}…"
cloudflared tunnel --url "http://localhost:${PORT}" >"$CF_LOG" 2>&1 &
CF_PID=$!

# Wait for the public URL to appear in cloudflared's output.
PUBLIC_URL=""
for _ in $(seq 1 40); do
  PUBLIC_URL="$(grep -Eo 'https://[a-z0-9.-]+\.trycloudflare\.com' "$CF_LOG" | head -n1 || true)"
  [[ -n "$PUBLIC_URL" ]] && break
  sleep 0.5
done

if [[ -z "$PUBLIC_URL" ]]; then
  echo "✗ Could not get a tunnel URL. cloudflared log:" >&2
  cat "$CF_LOG" >&2
  exit 1
fi

CAL_CB="${PUBLIC_URL}/api/calendar/google/callback"
GMAIL_CB="${PUBLIC_URL}/api/gmail/google/callback"
TENANT_CAL_CB="${PUBLIC_URL}/api/onboarding/google/calendar/callback"
TENANT_GMAIL_CB="${PUBLIC_URL}/api/onboarding/google/gmail/callback"

cat <<EOF

────────────────────────────────────────────────────────────────────
✓ Public site:   ${PUBLIC_URL}
  Setup wizard:  ${PUBLIC_URL}/setup

⚠ Google OAuth — add BOTH of these as Authorized redirect URIs on your
  OAuth client (console.cloud.google.com/apis/credentials), then Save:

    ${CAL_CB}
    ${GMAIL_CB}
    ${TENANT_CAL_CB}
    ${TENANT_GMAIL_CB}

  (Quick tunnels get a new URL each run, so re-add them each launch. For a
   stable URL, use a named cloudflared tunnel with your own domain.)
────────────────────────────────────────────────────────────────────

EOF

echo "› Starting API + site on :${PORT} (Ctrl-C to stop)…"
cd "$SERVER_DIR"
PUBLIC_BASE_URL="$PUBLIC_URL" PORT="$PORT" uv run python onboarding_api.py &
API_PID=$!

wait "$API_PID"
