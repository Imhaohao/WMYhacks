#!/usr/bin/env bash
#
# Serve the local Nemotron voicemail bot through the stable ngrok voice URL.
#
# Usage:
#   ./scripts/serve-voice-ngrok.sh
#
# The Twilio number's voice webhook should point at:
#   https://<saved-domain>/
#
# When ENABLE_ONBOARDING_API_ON_VOICE_WORKER=true (default here), the same
# public origin also serves tenant onboarding API routes under /api/*, so the
# Vercel app can use:
#   VITE_ONBOARDING_API_URL=https://<saved-domain>
#
# Set WEB_APP_URL to the deployed frontend origin so OAuth popups can post back
# to the Vercel page. Defaults to the current public Vercel alias.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="$ROOT/server"
DOMAIN_FILE="$ROOT/scripts/.ngrok-domain"
PORT="${PORT:-7860}"

DOMAIN="${NGROK_DOMAIN:-}"
if [[ -z "$DOMAIN" && -f "$DOMAIN_FILE" ]]; then
  DOMAIN="$(tr -d '[:space:]' < "$DOMAIN_FILE")"
fi
if [[ -z "$DOMAIN" ]]; then
  echo "No ngrok voice domain is configured." >&2
  echo "Pass NGROK_DOMAIN or save the hostname in scripts/.ngrok-domain." >&2
  exit 1
fi
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN%/}"

command -v ngrok >/dev/null 2>&1 || {
  echo "ngrok is not installed." >&2
  exit 1
}
if ! ngrok config check >/dev/null 2>&1; then
  echo "ngrok is not configured. Run: ngrok config add-authtoken <YOUR_TOKEN>" >&2
  exit 1
fi
if lsof -ti "tcp:${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port ${PORT} is already in use. Stop the existing voice bot first." >&2
  exit 1
fi

NGROK_PID=""
cleanup() {
  [[ -n "$NGROK_PID" ]] && kill "$NGROK_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Opening https://${DOMAIN} -> localhost:${PORT}"
ngrok http "$PORT" --url "https://${DOMAIN}" --log stdout >/tmp/gotchu-voice-ngrok.log 2>&1 &
NGROK_PID=$!

echo "Starting Nemotron voicemail bot in Twilio mode"
cd "$SERVER_DIR"
if [[ "${ENABLE_TWILIO_AUTOWIRE_DEMO_NUMBER:-true}" == "true" ]]; then
  PUBLIC_BASE_URL="https://${DOMAIN}" uv run python -c '
from dotenv import load_dotenv

load_dotenv(".env")
load_dotenv(".env.local", override=True)

import twilio_provisioning

result = twilio_provisioning.wire_configured_demo_number()
label = "Twilio webhook" if result.get("ok") else "Twilio webhook warning"
print(label + ": " + str(result.get("detail")))
'
fi
PUBLIC_BASE_URL="https://${DOMAIN}" \
WEB_APP_URL="${WEB_APP_URL:-https://web-omega-tawny-51.vercel.app}" \
ENABLE_ONBOARDING_API_ON_VOICE_WORKER="${ENABLE_ONBOARDING_API_ON_VOICE_WORKER:-true}" \
ENV=local uv run bot-nemotron.py -t twilio -x "$DOMAIN"
