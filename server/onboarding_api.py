"""FastAPI onboarding API for the Gotchu web UI."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import action_bridge
import contacts
import google_calendar
import google_gmail
import message_digest
import owner_config

# Where to bounce the user after the OAuth popup finishes. The popup closes
# itself, but this is the fallback if the browser blocks window.close().
WEB_APP_URL = os.getenv("WEB_APP_URL", "http://localhost:5173").rstrip("/")

SERVER_DIR = Path(__file__).parent
VCF_PATH = SERVER_DIR / "contacts.vcf"

load_dotenv(SERVER_DIR / ".env")
load_dotenv(SERVER_DIR / ".env.local", override=True)


class OwnerConfigBody(BaseModel):
    display_name: str | None = None
    owner_phone: str | None = None
    owner_email: str | None = None
    timezone: str | None = None
    manual_availability: str | None = None
    setup_complete: bool | None = None
    message_context_summary: str | None = None
    message_context_source: str | None = None


def _env_ok(name: str) -> tuple[bool, str]:
    val = os.getenv(name, "").strip()
    if val:
        return True, "configured"
    return False, f"{name} not set in .env"


def _owner_email_value() -> str:
    return os.getenv("OWNER_EMAIL", "").strip() or str(
        owner_config.load_owner_config().get("owner_email") or ""
    ).strip()


def _pending_action_counts() -> dict[str, int]:
    counts = {"email": 0, "calendar": 0}
    for action in action_bridge.pending_actions():
        kind = action.get("action")
        if kind == "send_email":
            counts["email"] += 1
        elif kind == "create_event":
            counts["calendar"] += 1
    return counts


def _contacts_sample_names(limit: int = 8) -> list[str]:
    names: list[str] = []
    for entry in contacts._INDEX.values():  # noqa: SLF001 — onboarding preview only
        n = entry.get("name")
        if n and n not in names:
            names.append(str(n))
        if len(names) >= limit:
            break
    return names


def _calendar_service_status(cfg: dict, pending: int) -> dict:
    """Calendar block for /api/services, backed by the real Google connection."""
    gstatus = google_calendar.status()
    connected = bool(gstatus.get("connected"))
    readable = bool(gstatus.get("readable", connected))
    tz = cfg.get("timezone") or os.getenv("OWNER_TZ", "America/Los_Angeles")
    detail = gstatus.get("detail", "")
    if connected and not readable:
        detail = gstatus.get("api_error") or detail
    return {
        "ok": readable,
        "mode": "google_oauth",
        "detail": detail,
        "connected": connected,
        "readable": readable,
        "configured": bool(gstatus.get("configured")),
        "target": gstatus.get("email") or "",
        "timezone": tz,
        "pending_actions": pending,
    }


def _gmail_service_status(email: str, smtp_ready: bool, pending: int) -> dict:
    """Gmail block for /api/services, preferring direct OAuth when connected."""
    gstatus = google_gmail.status()
    connected = bool(gstatus.get("connected"))
    if connected:
        return {
            "ok": True,
            "mode": "google_oauth",
            "detail": gstatus.get("detail", "Gmail connected."),
            "connected": True,
            "configured": bool(gstatus.get("configured")),
            "target": gstatus.get("email") or email,
            "pending_actions": pending,
        }
    if smtp_ready:
        return {
            "ok": True,
            "mode": "smtp",
            "detail": "SMTP is configured for real inbox delivery.",
            "connected": False,
            "configured": bool(gstatus.get("configured")),
            "target": email,
            "pending_actions": pending,
        }
    return {
        "ok": bool(email),
        "mode": "mcp_draft_bridge",
        "detail": (
            "Owner email is saved; connect Gmail for OAuth delivery or use the MCP draft bridge."
            if email
            else "Add your owner email first."
        ),
        "connected": False,
        "configured": bool(gstatus.get("configured")),
        "target": email,
        "pending_actions": pending,
    }


app = FastAPI(title="Gotchu Onboarding API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/owner-config")
def get_owner_config() -> dict:
    return owner_config.load_owner_config()


@app.post("/api/owner-config")
def post_owner_config(body: OwnerConfigBody) -> dict:
    patch = body.model_dump(exclude_none=True)
    # An owner editing the summary by hand reclaims it as "manual" so the next
    # auto-sync (force=False) won't clobber it until an explicit re-sync.
    if "message_context_summary" in patch and "message_context_source" not in patch:
        patch["message_context_source"] = "manual"
    return owner_config.save_owner_config(patch)


@app.post("/api/context/sync-messages")
def sync_messages() -> dict:
    """Refresh the redacted message digest from the producer (P2 seam).

    Returns topics/urgency summary + sync metadata only — never raw messages.
    """
    return message_digest.sync_message_context(force=True)


@app.get("/api/services")
def get_services() -> dict:
    """Return onboarding-facing service connection status.

    MCP connectors live in the agent client, not this FastAPI process, so this
    endpoint reports the local pieces the UI can honestly verify: owner config,
    SMTP readiness, outbox bridge readiness, and redacted Messages digest status.
    """
    cfg = owner_config.load_owner_config()
    email = _owner_email_value()
    smtp_ready = bool(os.getenv("GMAIL_APP_PASSWORD", "").strip() and email)
    counts = _pending_action_counts()
    source = cfg.get("message_context_source") or "manual"
    summary = str(cfg.get("message_context_summary") or "").strip()

    return {
        "gmail": _gmail_service_status(email, smtp_ready, counts["email"]),
        "calendar": _calendar_service_status(cfg, counts["calendar"]),
        "messages": {
            "ok": bool(summary),
            "mode": "redacted_digest",
            "detail": (
                "Redacted message context is available to the voice agent."
                if summary
                else "Sync from Messages or add a manual summary."
            ),
            "source": source,
            "synced_at": cfg.get("message_context_synced_at") or "",
            "preview": summary[:140],
        },
        "bridge": {
            "ok": True,
            "detail": "Run action_bridge.py after calls to emit Gmail/Calendar MCP requests.",
            "pending_actions": counts["email"] + counts["calendar"],
        },
    }


@app.get("/api/bridge/specs")
def get_bridge_specs(kind: str = "all") -> dict:
    """Return ready-to-fire MCP connector specs for queued outbox actions."""
    specs = []
    for action in action_bridge.pending_actions():
        action_kind = action.get("action")
        if kind == "gmail" and action_kind != "send_email":
            continue
        if kind == "calendar" and action_kind != "create_event":
            continue
        specs.append(action_bridge.connector_spec(action))
    return {
        "kind": kind,
        "count": len(specs),
        "specs": specs,
    }


# ─── Google Gmail OAuth ──────────────────────────────────────────────────────


@app.get("/api/gmail/google/status")
def gmail_google_status() -> dict:
    """Whether the owner's Gmail is connected (and to which account)."""
    return google_gmail.status()


@app.get("/api/gmail/google/auth-url")
def gmail_google_auth_url() -> dict:
    """Return the Google consent-screen URL for the setup wizard to open."""
    if not google_gmail.is_configured():
        return {
            "ok": False,
            "url": None,
            "detail": "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in server/.env, then retry.",
        }
    try:
        return {"ok": True, "url": google_gmail.auth_url()}
    except Exception as exc:
        return {"ok": False, "url": None, "detail": f"Could not start OAuth: {exc}"}


@app.get("/api/gmail/google/callback")
def gmail_google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """OAuth redirect target: exchange the code, store the token, close popup."""
    if error or not code:
        message = f"Authorization failed: {error}" if error else "No authorization code returned."
        return HTMLResponse(
            _oauth_result_html(False, message, service="Gmail", event="gmail-oauth-done"),
            status_code=400,
        )
    try:
        email = google_gmail.exchange_code(code, state=state)
        message = f"Connected {email}." if email else "Gmail connected."
        return HTMLResponse(_oauth_result_html(True, message, service="Gmail", event="gmail-oauth-done"))
    except Exception as exc:
        return HTMLResponse(
            _oauth_result_html(
                False,
                f"Token exchange failed: {exc}",
                service="Gmail",
                event="gmail-oauth-done",
            ),
            400,
        )


@app.post("/api/gmail/google/disconnect")
def gmail_google_disconnect() -> dict:
    """Forget the stored Gmail token."""
    removed = google_gmail.disconnect()
    return {"ok": True, "disconnected": removed}


# ─── Google Calendar OAuth ───────────────────────────────────────────────────


@app.get("/api/calendar/google/status")
def calendar_google_status() -> dict:
    """Whether the owner's Google Calendar is connected (and to which account)."""
    return google_calendar.status()


@app.get("/api/calendar/google/auth-url")
def calendar_google_auth_url() -> dict:
    """Return the Google consent-screen URL for the setup wizard to open."""
    if not google_calendar.is_configured():
        return {
            "ok": False,
            "url": None,
            "detail": "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in server/.env, then retry.",
        }
    try:
        return {"ok": True, "url": google_calendar.auth_url()}
    except Exception as exc:
        return {"ok": False, "url": None, "detail": f"Could not start OAuth: {exc}"}


@app.get("/api/calendar/google/callback")
def calendar_google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    """OAuth redirect target: exchange the code, store the token, close popup."""
    if error or not code:
        message = f"Authorization failed: {error}" if error else "No authorization code returned."
        return HTMLResponse(
            _oauth_result_html(False, message, service="Calendar", event="gcal-oauth-done"),
            status_code=400,
        )
    try:
        email = google_calendar.exchange_code(code, state=state)
        message = f"Connected {email}." if email else "Google Calendar connected."
        return HTMLResponse(
            _oauth_result_html(True, message, service="Calendar", event="gcal-oauth-done")
        )
    except Exception as exc:
        return HTMLResponse(
            _oauth_result_html(
                False,
                f"Token exchange failed: {exc}",
                service="Calendar",
                event="gcal-oauth-done",
            ),
            400,
        )


@app.post("/api/calendar/google/disconnect")
def calendar_google_disconnect() -> dict:
    """Forget the stored Google Calendar token."""
    removed = google_calendar.disconnect()
    return {"ok": True, "disconnected": removed}


def _oauth_result_html(success: bool, message: str, *, service: str, event: str) -> str:
    """A tiny self-closing page shown inside the OAuth popup."""
    color = "#16794a" if success else "#b42318"
    heading = f"✓ {service} connected" if success else "Connection failed"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{heading}</title></head>
<body style="font-family:system-ui,sans-serif;background:#f7f6f3;color:#2b2b2b;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0">
  <div style="text-align:center;max-width:420px;padding:32px">
    <h2 style="color:{color};margin:0 0 8px">{heading}</h2>
    <p style="color:#555;margin:0 0 20px">{message}</p>
    <p style="color:#999;font-size:13px">You can close this window and return to setup.</p>
  </div>
  <script>
    try {{ if (window.opener) window.opener.postMessage("{event}", "{WEB_APP_URL}"); }} catch (e) {{}}
    setTimeout(function () {{ try {{ window.close(); }} catch (e) {{}} }}, 1200);
  </script>
</body></html>"""


@app.post("/api/contacts/upload")
async def upload_contacts(file: UploadFile = File(...)) -> dict:
    content = await file.read()
    VCF_PATH.write_bytes(content)
    count = contacts.reload_index()
    return {
        "ok": True,
        "count": count,
        "summary": contacts.source_summary(),
        "sample_names": _contacts_sample_names(),
    }


@app.get("/api/health")
def health_check() -> dict:
    checks: dict[str, dict[str, str | bool]] = {}

    for key, env_name in [
        ("gradium", "GRADIUM_API_KEY"),
        ("nemotron_llm", "NEMOTRON_LLM_URL"),
        ("nemotron_stt", "NVIDIA_ASR_URL"),
        ("twilio", "TWILIO_ACCOUNT_SID"),
        ("gmail_smtp", "GMAIL_APP_PASSWORD"),
    ]:
        ok, detail = _env_ok(env_name)
        checks[key] = {"ok": ok, "detail": detail}

    owner_email = _owner_email_value()
    checks["owner_email"] = {
        "ok": bool(owner_email),
        "detail": "configured from onboarding" if owner_email else "add owner email in setup",
    }

    persist_table = os.getenv("PERSIST_DDB_TABLE", "ff-voicemails")
    checks["persistence"] = {
        "ok": True,
        "detail": f"DynamoDB table {persist_table} or local JSONL fallback",
    }

    cfg = owner_config.load_owner_config()
    checks["owner_profile"] = {
        "ok": bool(cfg.get("display_name") and cfg.get("owner_email")),
        "detail": "display name + email saved" if cfg.get("display_name") else "complete setup wizard",
    }

    required_ok = checks["owner_profile"]["ok"] and checks["persistence"]["ok"]

    return {
        "ok": required_ok,
        "checks": checks,
        "contacts": {
            "count": contacts.index_size(),
            "summary": contacts.source_summary(),
            "sample_names": _contacts_sample_names(),
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("onboarding_api:app", host="127.0.0.1", port=8787, reload=True)
