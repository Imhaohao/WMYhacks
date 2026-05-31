#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Real Google Calendar connection via OAuth 2.0 (owner self-connect).

This replaces the MCP-bridge calendar path: the owner clicks "Connect Google
Calendar" in the setup wizard, completes Google's consent screen, and the
backend stores a refresh token locally. From then on the voice bot can:

  * check the owner's real free/busy before booking a callback, and
  * create the tentative callback event directly on the owner's calendar.

Design notes
------------
* All Google libraries are imported lazily inside functions so importing this
  module never hard-fails (e.g. in test environments without the deps).
* The token is stored as an ``authorized_user`` JSON blob at
  ``GOOGLE_TOKEN_PATH`` (default ``server/google_token.json``). It holds the
  refresh token, so the connection survives restarts.
* Everything is defensive: read/write helpers return ``None`` instead of
  raising so a calendar hiccup never breaks a live call.
* Owner-only guardrail: this connects the *owner's* own Google account. Events
  always land on the owner's ``primary`` calendar.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

# Relax oauthlib's exact-scope check: Google routinely returns a superset of the
# requested scopes (e.g. adding the granular calendar.events scope), which would
# otherwise raise "Scope has changed" during token exchange.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

# Read + write access so we can both query free/busy and create callback events.
SCOPES = ["https://www.googleapis.com/auth/calendar"]

_DEFAULT_TOKEN_PATH = Path(__file__).parent / "google_token.json"
_DEFAULT_REDIRECT_URI = "http://localhost:8787/api/calendar/google/callback"

# PKCE: authorization_url() generates a code_verifier on the Flow instance. The
# callback is a separate HTTP request, so we stash verifier by OAuth state until
# exchange_code() runs (in-memory; lost on server restart — user reconnects).
_pending_pkce: dict[str, str] = {}


# ─── Configuration ───────────────────────────────────────────────────────────


def _token_path() -> Path:
    return Path(os.getenv("GOOGLE_TOKEN_PATH", str(_DEFAULT_TOKEN_PATH)))


def redirect_uri() -> str:
    return os.getenv("GOOGLE_OAUTH_REDIRECT_URI", _DEFAULT_REDIRECT_URI).strip()


def _client_config() -> dict[str, Any] | None:
    """Build a google-auth client config from env, or None if unconfigured."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri()],
        }
    }


def is_configured() -> bool:
    """True when GOOGLE_CLIENT_ID/SECRET are present so OAuth can start."""
    return _client_config() is not None


# ─── OAuth flow ──────────────────────────────────────────────────────────────


def _build_flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    config = _client_config()
    if config is None:
        raise RuntimeError(
            "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET in server/.env."
        )
    flow = Flow.from_client_config(config, scopes=SCOPES, state=state)
    flow.redirect_uri = redirect_uri()
    return flow


def auth_url() -> str:
    """Return the Google consent-screen URL for the owner to visit."""
    flow = _build_flow()
    url, state = flow.authorization_url(
        access_type="offline",      # ask for a refresh token
        include_granted_scopes="true",
        prompt="consent",           # force refresh-token issuance on re-connect
    )
    verifier = getattr(flow, "code_verifier", None)
    if state and verifier:
        _pending_pkce[state] = verifier
    return url


def exchange_code(code: str, *, state: str | None = None) -> str | None:
    """Exchange an OAuth ``code`` for tokens, persist them, return owner email."""
    if not state:
        raise ValueError("Missing OAuth state — restart Connect Google Calendar.")
    verifier = _pending_pkce.pop(state, None)
    if not verifier:
        raise ValueError(
            "OAuth session expired or server restarted — close this window and "
            "click Connect Google Calendar again."
        )
    flow = _build_flow(state=state)
    flow.code_verifier = verifier
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_credentials(creds)
    email = connected_email()
    logger.info(f"google_calendar: connected calendar for {email or 'unknown account'}")
    return email


# ─── Credential storage ──────────────────────────────────────────────────────


def _save_credentials(creds: Any) -> None:
    path = _token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(creds.to_json(), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - disk failure
        logger.warning(f"google_calendar: failed to persist token ({type(exc).__name__})")


def load_credentials() -> Any | None:
    """Load stored credentials, refreshing them if expired. None if absent."""
    path = _token_path()
    if not path.exists():
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        info = json.loads(path.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(info, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_credentials(creds)
        return creds if creds and creds.valid else creds
    except Exception as exc:
        logger.warning(f"google_calendar: could not load credentials ({type(exc).__name__})")
        return None


def is_connected() -> bool:
    creds = load_credentials()
    return bool(creds and getattr(creds, "valid", False))


def disconnect() -> bool:
    """Remove the stored token. Returns True if a token was deleted."""
    path = _token_path()
    if path.exists():
        try:
            path.unlink()
            return True
        except Exception as exc:  # pragma: no cover
            logger.warning(f"google_calendar: failed to delete token ({type(exc).__name__})")
    return False


# ─── Calendar service ────────────────────────────────────────────────────────


def _service() -> Any | None:
    creds = load_credentials()
    if not creds:
        return None
    try:
        from googleapiclient.discovery import build

        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as exc:
        logger.warning(f"google_calendar: failed to build service ({type(exc).__name__})")
        return None


def connected_email() -> str | None:
    """The owner's primary-calendar address (== their Google account email)."""
    service = _service()
    if not service:
        return None
    try:
        cal = service.calendars().get(calendarId="primary").execute()
        return cal.get("id")
    except Exception as exc:
        logger.warning(f"google_calendar: connected_email lookup failed ({type(exc).__name__})")
        return None


def is_free(start_iso: str | None, end_iso: str | None) -> bool | None:
    """Free/busy check for the owner's primary calendar.

    Returns True if the owner is free across [start, end], False if busy, and
    None when the window is unresolved or the calendar can't be reached (the
    caller treats None as "unknown" and still queues the request).
    """
    if not start_iso or not end_iso:
        return None
    service = _service()
    if not service:
        return None
    try:
        body = {
            "timeMin": start_iso,
            "timeMax": end_iso,
            "items": [{"id": "primary"}],
        }
        result = service.freebusy().query(body=body).execute()
        busy = result.get("calendars", {}).get("primary", {}).get("busy", [])
        return len(busy) == 0
    except Exception as exc:
        logger.warning(f"google_calendar: free/busy query failed ({type(exc).__name__})")
        return None


_last_api_error: str | None = None


def _http_error_detail(exc: Exception) -> str:
    """Turn a Google API failure into a short, actionable status string."""
    message = str(exc)
    if "accessNotConfigured" in message or "has not been used in project" in message:
        return (
            "Google Calendar API is disabled for this OAuth project. Enable "
            "Calendar API in Google Cloud Console, then retry."
        )
    if "insufficientPermissions" in message or "Insufficient Permission" in message:
        return "Calendar token lacks read permission — disconnect and reconnect in setup."
    return message[:240]


def probe_read_access() -> tuple[bool, str | None]:
    """Verify the stored token can read the primary calendar (not just refresh)."""
    global _last_api_error
    service = _service()
    if not service:
        _last_api_error = "Not connected — complete Connect Google Calendar in setup."
        return False, _last_api_error
    try:
        service.calendars().get(calendarId="primary").execute()
        _last_api_error = None
        return True, None
    except Exception as exc:
        _last_api_error = _http_error_detail(exc)
        logger.warning(f"google_calendar: read probe failed ({type(exc).__name__})")
        return False, _last_api_error


def list_events_for_date(
    date_iso: str,
    *,
    timezone: str | None = None,
) -> list[dict[str, Any]] | None:
    """Return a small, sanitized primary-calendar view for one local date.

    Titles, times, and visibility are retained for local reasoning. Descriptions,
    attendees, locations, conferencing links, and calendar IDs are deliberately
    omitted. Returns ``None`` when Calendar is unavailable.
    """
    try:
        day = date.fromisoformat(date_iso)
    except ValueError:
        return None
    service = _service()
    if not service:
        return None
    tz = ZoneInfo((timezone or os.getenv("OWNER_TZ") or "America/Los_Angeles").strip())
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    try:
        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
    except Exception as exc:
        global _last_api_error
        _last_api_error = _http_error_detail(exc)
        logger.warning(f"google_calendar: event-list query failed ({type(exc).__name__})")
        return None

    events = []
    for item in result.get("items", []):
        if item.get("status") == "cancelled":
            continue
        start_data = item.get("start") or {}
        end_data = item.get("end") or {}
        events.append(
            {
                "summary": str(item.get("summary") or "another commitment").strip(),
                "start": start_data.get("dateTime") or start_data.get("date"),
                "end": end_data.get("dateTime") or end_data.get("date"),
                "all_day": "date" in start_data,
                "visibility": item.get("visibility") or "default",
            }
        )
    return events


_UNSAFE_TITLE = re.compile(r"https?://|www\.|[\w.+-]+@[\w.-]+\.\w+|\+?\d[\d(). -]{6,}\d")


def _caller_facing_title(event: dict[str, Any]) -> str:
    """Reduce one event to a caller-safe reason without leaking event metadata."""
    summary = str(event.get("summary") or "").strip()
    if (
        not summary
        or event.get("visibility") == "private"
        or len(summary) > 80
        or _UNSAFE_TITLE.search(summary)
    ):
        return "another commitment"
    return summary


def explain_date_conflict(
    date_iso: str,
    *,
    caller_name: str | None = None,
    topic: str | None = None,
    timezone: str | None = None,
) -> dict[str, Any] | None:
    """Derive one caller-facing reason from a date's sanitized event list.

    The caller is already contact-gated by ``persona_tools``. This helper keeps
    unrelated schedule details private: it returns at most one competing
    commitment title, never the full calendar.
    """
    events = list_events_for_date(date_iso, timezone=timezone)
    if events is None:
        return None

    query_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", f"{caller_name or ''} {topic or ''}".lower())
        if len(token) >= 3
    }

    def is_subject(event: dict[str, Any]) -> bool:
        summary = str(event.get("summary") or "").lower()
        if "birthday" in summary and ("birthday" in query_tokens or caller_name):
            return not query_tokens or any(token in summary for token in query_tokens)
        return bool(query_tokens and sum(token in summary for token in query_tokens) >= 2)

    subject = next((event for event in events if is_subject(event)), None)
    alternatives = [event for event in events if event is not subject and "birthday" not in str(event.get("summary") or "").lower()]
    # Prefer timed commitments over all-day notes and use the longest title as a
    # deterministic tie-breaker; it is usually the most descriptive reason.
    alternatives.sort(
        key=lambda event: (bool(event.get("all_day")), -len(str(event.get("summary") or "")))
    )
    reason = _caller_facing_title(alternatives[0]) if alternatives else None
    return {
        "date": date_iso,
        "related_event_found": subject is not None,
        "reason": reason,
    }


def create_event(
    summary: str,
    start_iso: str | None,
    end_iso: str | None,
    description: str = "",
    timezone: str | None = None,
) -> dict[str, Any] | None:
    """Create a tentative callback event on the owner's primary calendar.

    Returns ``{"id", "htmlLink"}`` on success, or None if not connected / the
    slot is unresolved / the API call fails (caller then falls back to outbox).
    """
    if not start_iso or not end_iso:
        return None
    service = _service()
    if not service:
        return None
    tz = (timezone or os.getenv("OWNER_TZ") or "America/Los_Angeles").strip()
    event_body = {
        "summary": f"[Tentative] {summary}",
        "description": description,
        "start": {"dateTime": start_iso, "timeZone": tz},
        "end": {"dateTime": end_iso, "timeZone": tz},
        "status": "tentative",
        "colorId": "8",  # Graphite — visually reads as "soft / proposed"
        "visibility": "private",
    }
    try:
        created = service.events().insert(calendarId="primary", body=event_body).execute()
        logger.info(f"google_calendar: created event {created.get('id')}")
        return {"id": created.get("id"), "htmlLink": created.get("htmlLink")}
    except Exception as exc:
        logger.warning(f"google_calendar: event creation failed ({type(exc).__name__})")
        return None


def status() -> dict[str, Any]:
    """Connection status for the onboarding UI."""
    if not is_configured():
        return {
            "connected": False,
            "configured": False,
            "readable": False,
            "email": None,
            "detail": "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in server/.env.",
            "api_error": None,
        }
    if not is_connected():
        return {
            "connected": False,
            "configured": True,
            "readable": False,
            "email": None,
            "detail": "Click Connect Google Calendar to authorize your account.",
            "api_error": None,
        }
    readable, api_error = probe_read_access()
    email = connected_email() if readable else None
    if readable:
        detail = f"Connected to {email}." if email else "Google Calendar connected."
    else:
        detail = api_error or "Calendar token present but read access failed."
    return {
        "connected": True,
        "configured": True,
        "readable": readable,
        "email": email,
        "detail": detail,
        "api_error": api_error,
    }
