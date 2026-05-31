#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Real Gmail connection via OAuth 2.0 (owner self-connect).

This mirrors ``google_calendar.py``: the owner clicks "Connect Gmail" in the
setup wizard, completes Google's consent screen, and the backend stores a
refresh token locally. From then on the voice bot can send the voicemail
summary from the owner's Gmail account to the owner's inbox.

Design notes
------------
* Google libraries are imported lazily so this module is safe to import in
  tests and local shells without configured credentials.
* The token is stored as an ``authorized_user`` JSON blob at
  ``GOOGLE_GMAIL_TOKEN_PATH`` (default ``server/google_gmail_token.json``).
* Read access is used only by ``ingest.gmail_context`` to derive local,
  aggregate writing-style signals from the owner's sent mail.
* Everything is defensive: send/draft helpers return ``None`` instead of
  raising so Gmail issues never break a live call.
* Owner-only guardrail lives in ``actions.py``: callers cannot pick recipients.
"""

from __future__ import annotations

import base64
import json
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from loguru import logger

# See google_calendar.py for why this is needed.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]

_DEFAULT_TOKEN_PATH = Path(__file__).parent / "google_gmail_token.json"
_DEFAULT_REDIRECT_URI = "http://localhost:8787/api/gmail/google/callback"

# PKCE verifier storage between auth-url and callback requests.
_pending_pkce: dict[str, str] = {}


# --- Configuration ---------------------------------------------------------


def _token_path() -> Path:
    return Path(os.getenv("GOOGLE_GMAIL_TOKEN_PATH", str(_DEFAULT_TOKEN_PATH)))


def redirect_uri() -> str:
    return os.getenv("GOOGLE_GMAIL_REDIRECT_URI", _DEFAULT_REDIRECT_URI).strip()


def _client_config() -> dict[str, Any] | None:
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
    return _client_config() is not None


# --- OAuth flow ------------------------------------------------------------


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
    flow = _build_flow()
    url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    verifier = getattr(flow, "code_verifier", None)
    if state and verifier:
        _pending_pkce[state] = verifier
    return url


def exchange_code(code: str, *, state: str | None = None) -> str | None:
    if not state:
        raise ValueError("Missing OAuth state - restart Connect Gmail.")
    verifier = _pending_pkce.pop(state, None)
    if not verifier:
        raise ValueError(
            "OAuth session expired or server restarted - close this window and "
            "click Connect Gmail again."
        )
    flow = _build_flow(state=state)
    flow.code_verifier = verifier
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_credentials(creds)
    email = connected_email()
    logger.info(f"google_gmail: connected Gmail for {email or 'unknown account'}")
    return email


# --- Credential storage ----------------------------------------------------


def _save_credentials(creds: Any) -> None:
    path = _token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(creds.to_json(), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - disk failure
        logger.warning(f"google_gmail: failed to persist token ({type(exc).__name__})")


def load_credentials() -> Any | None:
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
        logger.warning(f"google_gmail: could not load credentials ({type(exc).__name__})")
        return None


def is_connected() -> bool:
    creds = load_credentials()
    return bool(creds and getattr(creds, "valid", False))


def disconnect() -> bool:
    path = _token_path()
    if path.exists():
        try:
            path.unlink()
            return True
        except Exception as exc:  # pragma: no cover
            logger.warning(f"google_gmail: failed to delete token ({type(exc).__name__})")
    return False


# --- Gmail service ---------------------------------------------------------


def _service() -> Any | None:
    creds = load_credentials()
    if not creds:
        return None
    try:
        from googleapiclient.discovery import build

        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as exc:
        logger.warning(f"google_gmail: failed to build service ({type(exc).__name__})")
        return None


def connected_email() -> str | None:
    creds = load_credentials()
    if not creds:
        return None
    try:
        from google.auth.transport.requests import AuthorizedSession

        session = AuthorizedSession(creds)
        profile = session.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=10).json()
        return profile.get("email")
    except Exception as exc:
        logger.warning(f"google_gmail: connected_email lookup failed ({type(exc).__name__})")
        return None


def _raw_message(
    to_addr: str,
    subject: str,
    body: str,
    sender: str | None = None,
) -> str:
    msg = EmailMessage()
    msg["To"] = to_addr
    if sender:
        msg["From"] = sender
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def send_email(
    to_addr: str,
    subject: str,
    body: str,
    sender: str | None = None,
) -> dict[str, Any] | None:
    """Send an email through the connected owner's Gmail account."""
    service = _service()
    if not service:
        return None
    try:
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": _raw_message(to_addr, subject, body, sender)})
            .execute()
        )
        logger.info(f"google_gmail: sent message {sent.get('id')}")
        return {"id": sent.get("id"), "threadId": sent.get("threadId")}
    except Exception as exc:
        logger.warning(f"google_gmail: send failed ({type(exc).__name__})")
        return None


def create_draft(
    to_addr: str,
    subject: str,
    body: str,
    sender: str | None = None,
) -> dict[str, Any] | None:
    """Create a Gmail draft. Useful as a non-sending fallback or demo check."""
    service = _service()
    if not service:
        return None
    try:
        draft = (
            service.users()
            .drafts()
            .create(
                userId="me",
                body={"message": {"raw": _raw_message(to_addr, subject, body, sender)}},
            )
            .execute()
        )
        logger.info(f"google_gmail: created draft {draft.get('id')}")
        return {"id": draft.get("id"), "message": draft.get("message")}
    except Exception as exc:
        logger.warning(f"google_gmail: draft creation failed ({type(exc).__name__})")
        return None


def _decode_body_data(data: str) -> str:
    """Decode Gmail's URL-safe base64 message body, tolerating missing padding."""
    if not data:
        return ""
    try:
        padded = data + ("=" * (-len(data) % 4))
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
    except (ValueError, TypeError):
        return ""


def _plain_text_from_payload(payload: dict[str, Any]) -> str:
    """Extract text/plain content from a Gmail API payload tree."""
    if payload.get("filename"):
        return ""
    parts = payload.get("parts") or []
    texts = [_plain_text_from_payload(part) for part in parts if isinstance(part, dict)]
    texts = [text for text in texts if text]
    if texts:
        return "\n".join(texts)
    if payload.get("mimeType") != "text/plain":
        return ""
    return _decode_body_data(str((payload.get("body") or {}).get("data") or ""))


def _strip_quoted_reply(text: str) -> str:
    """Keep the owner's newly written portion of a sent email."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        if stripped.startswith("-----Original Message-----"):
            break
        if stripped.startswith("On ") and stripped.endswith(" wrote:"):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def read_sent_bodies(max_messages: int = 100) -> list[str]:
    """Read owner-authored sent-mail bodies for local style derivation only."""
    service = _service()
    if not service:
        return []
    try:
        summaries = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["SENT"], maxResults=max_messages)
            .execute()
            .get("messages", [])
        )
        bodies: list[str] = []
        for summary in summaries:
            message_id = summary.get("id")
            if not message_id:
                continue
            message = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            text = _plain_text_from_payload(message.get("payload") or {})
            text = _strip_quoted_reply(text or str(message.get("snippet") or ""))
            if text:
                bodies.append(text)
        return bodies
    except Exception as exc:
        logger.warning(f"google_gmail: sent-mail read failed ({type(exc).__name__})")
        return []


def status() -> dict[str, Any]:
    if not is_configured():
        return {
            "connected": False,
            "configured": False,
            "email": None,
            "detail": "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in server/.env.",
        }
    if not is_connected():
        return {
            "connected": False,
            "configured": True,
            "email": None,
            "detail": "Click Connect Gmail to authorize your account.",
        }
    email = connected_email()
    return {
        "connected": True,
        "configured": True,
        "email": email,
        "detail": f"Connected to {email}." if email else "Gmail connected.",
    }
