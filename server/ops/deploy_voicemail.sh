#!/usr/bin/env bash
# Deploy the voicemail bot to the always-on Mac over Tailscale.
#
# One command from THIS Mac (which holds the repo and all secrets):
#
#     bash server/ops/deploy_voicemail.sh
#
# The always-on box ("compute-box") ends up running the bot as a launchd job
# that auto-restarts, reachable from Twilio through a free Tailscale Funnel URL.
#
# Requirements on the box: macOS, Tailscale signed in (and the tailnet must
# allow Funnel for that node), SSH key auth already working.

set -euo pipefail

BOX_ALIAS="${BOX_ALIAS:-compute-box}"
BOX_TS_IP="${BOX_TS_IP:-100.85.119.16}"
BOX_LAN_IP="${BOX_LAN_IP:-10.0.0.229}"
PHONE_NUMBER="${PHONE_NUMBER:-+16506444731}"
REMOTE_DIR="${REMOTE_DIR:-voicemail-agent}"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SERVER_DIR="$REPO_ROOT/server"

log() { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[deploy] FAILED:\033[0m %s\n' "$*" >&2; exit 1; }

ssh_one() {
    ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "$@"
}

# ── 1. Find the box ────────────────────────────────────────────────────────────
BOX_HOST=""
for host in "$BOX_ALIAS" "$BOX_TS_IP" "$BOX_LAN_IP"; do
    if ssh -o BatchMode=yes -o ConnectTimeout=6 "$host" true 2>/dev/null; then
        BOX_HOST="$host"
        break
    fi
done
if [[ -z "$BOX_HOST" ]]; then
    die "always-on box unreachable. Turn it on, start Tailscale there AND on this Mac, then rerun."
fi
log "box reachable at $BOX_HOST"

# ── 2. Sync code + secrets ─────────────────────────────────────────────────────
log "syncing server/ -> $BOX_HOST:~/$REMOTE_DIR/server"
ssh_one "$BOX_HOST" mkdir -p ~/"$REMOTE_DIR/server"
rsync -az --delete \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'aws_store' \
    --exclude 'outbox' \
    --exclude '.pytest_cache' \
    "$SERVER_DIR/" "$BOX_HOST":~/"$REMOTE_DIR/server/"
log "sync complete"

# ── 3. uv + deps ───────────────────────────────────────────────────────────────
UV_PATH=$(ssh_one "$BOX_HOST" "bash -lc 'command -v uv || ls \"\$HOME/.local/bin/uv\" 2>/dev/null || ls /opt/homebrew/bin/uv 2>/dev/null || true'")
if [[ -z "$UV_PATH" ]]; then
    log "uv not found on box — installing"
    if ! ssh_one "$BOX_HOST" "bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'"; then
        die "uv install failed on box"
    fi
    UV_PATH=$(ssh_one "$BOX_HOST" "bash -lc 'command -v uv || ls \"\$HOME/.local/bin/uv\" 2>/dev/null || ls /opt/homebrew/bin/uv 2>/dev/null'")
fi
[[ -n "$UV_PATH" ]] || die "could not locate uv on box"
log "uv: $UV_PATH"
ssh_one "$BOX_HOST" "cd ~/$REMOTE_DIR/server && $UV_PATH sync" || die "uv sync failed on box"

# ── 4. Tailscale Funnel (free public URL for Twilio) ───────────────────────────
log "enabling Tailscale Funnel for port 7860"
TS_CLI=$(ssh_one "$BOX_HOST" "bash -lc 'command -v tailscale || ls /opt/homebrew/bin/tailscale 2>/dev/null || ls /Applications/Tailscale.app/Contents/MacOS/Tailscale 2>/dev/null || true'")
[[ -n "$TS_CLI" ]] || die "Tailscale CLI not found on the box"
if ! ssh_one "$BOX_HOST" "$TS_CLI funnel --bg 7860"; then
    die "Funnel enable failed. Give the node the 'funnel' attribute (Tailscale admin console -> Access Controls -> nodeAttrs, then 'tailscale set --funnel' on the box) and rerun."
fi
DNS_NAME=$(ssh_one "$BOX_HOST" "$TS_CLI status --json" | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
FUNNEL_URL="https://$DNS_NAME"
log "funnel serving: $FUNNEL_URL"

# ── 5. Box-side config: PUBLIC_BASE_URL, Twilio webhook, launchd job ───────────
ssh_one "$BOX_HOST" "cd ~/$REMOTE_DIR/server && DEPLOY_URL='$FUNNEL_URL' DEPLOY_UV='$UV_PATH' DEPLOY_PHONE='$PHONE_NUMBER' $UV_PATH run python ops/deploy_on_box.py"

# ── 6. (Re)start the bot under launchd ─────────────────────────────────────────
ssh_one "$BOX_HOST" "bash -lc 'launchctl bootout gui/\$UID ~/Library/LaunchAgents/com.voicemail.elevenlabs.plist 2>/dev/null || true'"
ssh_one "$BOX_HOST" "bash -lc 'launchctl bootstrap gui/\$UID ~/Library/LaunchAgents/com.voicemail.elevenlabs.plist'"
ssh_one "$BOX_HOST" "bash -lc 'launchctl kickstart -k gui/\$UID/com.voicemail.elevenlabs'"

# ── 7. Verify ──────────────────────────────────────────────────────────────────
sleep 25
HTTP_CODE=$(ssh_one "$BOX_HOST" 'curl -s -o /dev/null -w "%{http_code}" http://localhost:7860/ || true')
log "health check: HTTP $HTTP_CODE (from the box itself)"
if [[ "$HTTP_CODE" != "200" ]]; then
    log "bot not up yet — recent log:"
    ssh_one "$BOX_HOST" 'tail -30 ~/Library/Logs/voicemail-bot.err.log 2>/dev/null; tail -20 ~/Library/Logs/voicemail-bot.log 2>/dev/null' || true
fi

cat <<EOF

Deploy done.

  Bot:        $FUNNEL_URL           (Tailscale Funnel, free)
  Line:       $PHONE_NUMBER   — forward YOUR phone to this number (dial *71$PHONE_NUMBER once)
  Webhook:    Twilio voice -> $FUNNEL_URL/   (set by the API just now)
  Restart:    launches automatically; rerun this script to upgrade code
  Logs:       ssh $BOX_HOST 'tail -f ~/Library/Logs/voicemail-bot.log'

Test: call your own phone number. It forwards to Twilio, which posts to the
funnel URL, and the bot on the always-on box answers with your cloned voice.
EOF
