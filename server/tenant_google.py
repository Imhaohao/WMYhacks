"""Tenant-scoped Google OAuth and token storage.

The demo owner's ``google_gmail.py`` / ``google_calendar.py`` modules store one
local token file. Public onboarding needs a harder boundary: every tenant gets
their own Google token, stored outside the tenant profile record so public
session reads can never leak refresh tokens.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
from datetime import UTC, date, datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from loguru import logger

_ENV_DIR = Path(__file__).resolve().parent
load_dotenv(_ENV_DIR / ".env", override=True)
load_dotenv(_ENV_DIR / ".env.local", override=True)

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

GMAIL = "gmail"
CALENDAR = "calendar"
SERVICES = {GMAIL, CALENDAR}
SCOPES = {
    GMAIL: [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/gmail.readonly",
    ],
    CALENDAR: ["https://www.googleapis.com/auth/calendar"],
}

_DEFAULT_LOCAL_PATH = Path(__file__).parent / "aws_store" / "tenant_google_tokens.json"
_DEFAULT_REDIRECTS = {
    GMAIL: "http://localhost:8787/api/onboarding/google/gmail/callback",
    CALENDAR: "http://localhost:8787/api/onboarding/google/calendar/callback",
}


class TenantGoogleUnavailable(RuntimeError):
    """Raised when the tenant Google vault cannot be reached."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _check_service(service: str) -> str:
    normalized = service.strip().lower()
    if normalized not in SERVICES:
        raise ValueError(f"Unsupported Google service: {service}")
    return normalized


def redirect_uri(service: str) -> str:
    service = _check_service(service)
    base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/api/onboarding/google/{service}/callback"
    env_name = f"TENANT_GOOGLE_{service.upper()}_REDIRECT_URI"
    return os.getenv(env_name, _DEFAULT_REDIRECTS[service]).strip()


def _client_config(service: str) -> dict[str, Any] | None:
    _check_service(service)
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
            "redirect_uris": [redirect_uri(service)],
        }
    }


def is_configured(service: str) -> bool:
    return _client_config(service) is not None


def _build_flow(service: str, *, state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    service = _check_service(service)
    config = _client_config(service)
    if config is None:
        raise RuntimeError("Google OAuth is not configured.")
    flow = Flow.from_client_config(config, scopes=SCOPES[service], state=state)
    flow.redirect_uri = redirect_uri(service)
    return flow


def _vault_mode() -> str:
    if os.getenv("TENANT_GOOGLE_TOKEN_FORCE_LOCAL", "").strip().lower() == "true":
        return "local"
    explicit = os.getenv("TENANT_GOOGLE_TOKEN_STORE", "").strip().lower()
    if explicit:
        return explicit
    if os.getenv("TENANT_STORE_REQUIRE_CLOUD", "").strip().lower() == "true":
        return "secretsmanager"
    return "local"


def _secret_prefix() -> str:
    return os.getenv("TENANT_GOOGLE_SECRET_PREFIX", "gotchu/tenant-google").strip("/")


def _local_path() -> Path:
    return Path(os.getenv("TENANT_GOOGLE_TOKEN_LOCAL_PATH", str(_DEFAULT_LOCAL_PATH)))


def _tenant_key(tenant_id: str, service: str) -> str:
    service = _check_service(service)
    return f"{tenant_id}:{service}"


def _state_key(state: str) -> str:
    return f"oauth-state:{state}"


class _Vault:
    def __init__(self) -> None:
        self.kind = _vault_mode()
        self._client: Any | None = None
        if self.kind == "secretsmanager":
            try:
                import boto3  # noqa: PLC0415

                session = boto3.Session()
                if session.get_credentials() is None:
                    raise TenantGoogleUnavailable("AWS credentials are unavailable.")
                self._client = session.client("secretsmanager")
            except Exception as exc:
                raise TenantGoogleUnavailable(
                    f"Tenant Google secret storage is unavailable ({type(exc).__name__})."
                ) from exc
        elif self.kind != "local":
            raise TenantGoogleUnavailable(f"Unsupported tenant Google token store: {self.kind}")
        logger.info("tenant_google: using {} token vault", self.kind)

    def _secret_name(self, key: str) -> str:
        return f"{_secret_prefix()}/{key}"

    def get(self, key: str) -> dict[str, Any] | None:
        if self.kind == "secretsmanager":
            try:
                body = self._client.get_secret_value(SecretId=self._secret_name(key))  # type: ignore[union-attr]
                return json.loads(body.get("SecretString") or "{}")
            except Exception as exc:
                if type(exc).__name__ in {"ResourceNotFoundException", "ClientError"}:
                    return None
                raise
        return self._read_local().get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        if self.kind == "secretsmanager":
            payload = json.dumps(value)
            name = self._secret_name(key)
            try:
                self._client.put_secret_value(SecretId=name, SecretString=payload)  # type: ignore[union-attr]
            except Exception as exc:
                if type(exc).__name__ not in {"ResourceNotFoundException", "ClientError"}:
                    raise
                self._client.create_secret(Name=name, SecretString=payload)  # type: ignore[union-attr]
            return
        data = self._read_local()
        data[key] = value
        path = _local_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def delete(self, key: str) -> bool:
        if self.kind == "secretsmanager":
            name = self._secret_name(key)
            try:
                self._client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)  # type: ignore[union-attr]
                return True
            except Exception:
                return False
        data = self._read_local()
        existed = key in data
        data.pop(key, None)
        path = _local_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return existed

    def _read_local(self) -> dict[str, dict[str, Any]]:
        path = _local_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


_vault: _Vault | None = None


def _get_vault() -> _Vault:
    global _vault
    if _vault is None:
        _vault = _Vault()
    return _vault


def vault_kind() -> str:
    return _get_vault().kind


def save_oauth_state(state: str, payload: dict[str, Any]) -> None:
    _get_vault().put(_state_key(state), payload)


def pop_oauth_state(state: str) -> dict[str, Any] | None:
    key = _state_key(state)
    payload = _get_vault().get(key)
    _get_vault().delete(key)
    return payload


def create_auth_url(tenant_id: str, service: str) -> str:
    service = _check_service(service)
    state = secrets.token_urlsafe(32)
    flow = _build_flow(service, state=state)
    url, returned_state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    verifier = getattr(flow, "code_verifier", None)
    if not verifier:
        raise RuntimeError("Google OAuth did not provide a PKCE verifier.")
    save_oauth_state(
        returned_state or state,
        {
            "tenant_id": tenant_id,
            "service": service,
            "code_verifier": verifier,
            "created_at": _now_iso(),
        },
    )
    return url


def _connected_email_for_credentials(service: str, creds: Any) -> str:
    service = _check_service(service)
    if service == GMAIL:
        from google.auth.transport.requests import AuthorizedSession

        session = AuthorizedSession(creds)
        profile = session.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=10).json()
        return str(profile.get("email") or "")

    from googleapiclient.discovery import build

    calendar = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return str(calendar.calendars().get(calendarId="primary").execute().get("id") or "")


def exchange_code(service: str, code: str, state: str) -> tuple[str, str]:
    service = _check_service(service)
    pending = pop_oauth_state(state)
    if not pending or pending.get("service") != service:
        raise ValueError("OAuth session expired or does not match this Google service.")
    tenant_id = str(pending.get("tenant_id") or "")
    verifier = str(pending.get("code_verifier") or "")
    if not tenant_id or not verifier:
        raise ValueError("OAuth session is incomplete.")
    flow = _build_flow(service, state=state)
    flow.code_verifier = verifier
    flow.fetch_token(code=code)
    creds = flow.credentials
    email = _connected_email_for_credentials(service, creds)
    save_token_json(tenant_id, service, creds.to_json(), email=email)
    logger.info("tenant_google: connected {} for tenant {}", service, tenant_id)
    return tenant_id, email


def save_token_json(tenant_id: str, service: str, token_json: str, *, email: str = "") -> None:
    service = _check_service(service)
    _get_vault().put(
        _tenant_key(tenant_id, service),
        {
            "tenant_id": tenant_id,
            "service": service,
            "token_json": token_json,
            "email": email,
            "updated_at": _now_iso(),
        },
    )


def load_record(tenant_id: str, service: str) -> dict[str, Any] | None:
    return _get_vault().get(_tenant_key(tenant_id, service))


def is_connected(tenant_id: str, service: str) -> bool:
    record = load_record(tenant_id, service)
    return bool(record and record.get("token_json"))


def status(tenant_id: str, service: str) -> dict[str, Any]:
    service = _check_service(service)
    record = load_record(tenant_id, service) or {}
    return {
        "configured": is_configured(service),
        "connected": bool(record.get("token_json")),
        "email": record.get("email") or "",
        "storage": vault_kind(),
    }


def disconnect(tenant_id: str, service: str) -> bool:
    return _get_vault().delete(_tenant_key(tenant_id, service))


def _credentials(tenant_id: str, service: str) -> Any | None:
    service = _check_service(service)
    record = load_record(tenant_id, service)
    if not record or not record.get("token_json"):
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        info = json.loads(str(record["token_json"]))
        creds = Credentials.from_authorized_user_info(info, SCOPES[service])
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            save_token_json(tenant_id, service, creds.to_json(), email=str(record.get("email") or ""))
        return creds if creds and creds.valid else creds
    except Exception as exc:
        logger.warning("tenant_google: could not load {} token ({})", service, type(exc).__name__)
        return None


def _google_service(tenant_id: str, service: str) -> Any | None:
    creds = _credentials(tenant_id, service)
    if not creds:
        return None
    try:
        from googleapiclient.discovery import build

        api_name = "gmail" if service == GMAIL else "calendar"
        api_version = "v1" if service == GMAIL else "v3"
        return build(api_name, api_version, credentials=creds, cache_discovery=False)
    except Exception as exc:
        logger.warning("tenant_google: failed to build {} service ({})", service, type(exc).__name__)
        return None


def _raw_message(to_addr: str, subject: str, body: str, sender: str | None = None) -> str:
    msg = EmailMessage()
    msg["To"] = to_addr
    if sender:
        msg["From"] = sender
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def send_gmail(
    tenant_id: str,
    to_addr: str,
    subject: str,
    body: str,
    sender: str | None = None,
) -> dict[str, Any] | None:
    service = _google_service(tenant_id, GMAIL)
    if not service:
        return None
    try:
        sent = (
            service.users()
            .messages()
            .send(userId="me", body={"raw": _raw_message(to_addr, subject, body, sender)})
            .execute()
        )
        return {"id": sent.get("id"), "threadId": sent.get("threadId")}
    except Exception as exc:
        logger.warning("tenant_google: Gmail send failed ({})", type(exc).__name__)
        return None


def list_recent_gmail(
    tenant_id: str,
    max_results: int = 5,
    query: str = "",
) -> list[dict[str, Any]]:
    """Read lightweight tenant-owned Gmail message summaries."""
    service = _google_service(tenant_id, GMAIL)
    if not service:
        return []
    try:
        list_kwargs: dict[str, Any] = {"userId": "me", "maxResults": max_results}
        if query.strip():
            list_kwargs["q"] = query.strip()
        else:
            list_kwargs["labelIds"] = ["INBOX"]
        summaries = (
            service.users().messages().list(**list_kwargs).execute().get("messages", [])
        )
        out: list[dict[str, Any]] = []
        for summary in summaries:
            mid = summary.get("id")
            if not mid:
                continue
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            headers = (msg.get("payload") or {}).get("headers") or []
            out.append(
                {
                    "from": _header(headers, "From"),
                    "subject": _header(headers, "Subject"),
                    "date": _header(headers, "Date"),
                    "snippet": str(msg.get("snippet") or ""),
                    "unread": "UNREAD" in (msg.get("labelIds") or []),
                }
            )
        return out
    except Exception as exc:
        logger.warning("tenant_google: inbox read failed ({})", type(exc).__name__)
        return []


def calendar_is_free(tenant_id: str, start_iso: str | None, end_iso: str | None) -> bool | None:
    if not start_iso or not end_iso:
        return None
    service = _google_service(tenant_id, CALENDAR)
    if not service:
        return None
    try:
        result = (
            service.freebusy()
            .query(body={"timeMin": start_iso, "timeMax": end_iso, "items": [{"id": "primary"}]})
            .execute()
        )
        busy = result.get("calendars", {}).get("primary", {}).get("busy", [])
        return len(busy) == 0
    except Exception as exc:
        logger.warning("tenant_google: calendar free/busy failed ({})", type(exc).__name__)
        return None


def create_calendar_event(
    tenant_id: str,
    summary: str,
    start_iso: str | None,
    end_iso: str | None,
    description: str = "",
    timezone: str | None = None,
) -> dict[str, Any] | None:
    if not start_iso or not end_iso:
        return None
    service = _google_service(tenant_id, CALENDAR)
    if not service:
        return None
    tz = (timezone or "America/Los_Angeles").strip()
    event_body = {
        "summary": f"[Tentative] {summary}",
        "description": f"TENTATIVE - created by Gotchu.\n\n{description}".strip(),
        "start": {"dateTime": start_iso, "timeZone": tz},
        "end": {"dateTime": end_iso, "timeZone": tz},
        "colorId": "8",
    }
    try:
        event = (
            service.events()
            .insert(calendarId="primary", body=event_body, sendUpdates="none")
            .execute()
        )
        return {"id": event.get("id"), "htmlLink": event.get("htmlLink")}
    except Exception as exc:
        logger.warning("tenant_google: calendar create failed ({})", type(exc).__name__)
        return None


def _calendar_events_for_date(
    tenant_id: str,
    date_iso: str,
    *,
    timezone: str | None = None,
) -> list[dict[str, Any]] | None:
    try:
        day = date.fromisoformat(date_iso)
    except ValueError:
        return None
    service = _google_service(tenant_id, CALENDAR)
    if not service:
        return None
    tz = ZoneInfo((timezone or "America/Los_Angeles").strip())
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
        logger.warning("tenant_google: calendar event-list failed ({})", type(exc).__name__)
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


def list_calendar_events_for_date(
    tenant_id: str,
    date_iso: str,
    *,
    timezone: str | None = None,
) -> list[dict[str, Any]] | None:
    """Read lightweight tenant-owned primary-calendar event summaries."""
    return _calendar_events_for_date(tenant_id, date_iso, timezone=timezone)


_UNSAFE_TITLE = re.compile(r"https?://|www\.|[\w.+-]+@[\w.-]+\.\w+|\+?\d[\d(). -]{6,}\d")


def _caller_facing_title(event: dict[str, Any]) -> str:
    summary = str(event.get("summary") or "").strip()
    if (
        not summary
        or event.get("visibility") == "private"
        or len(summary) > 80
        or _UNSAFE_TITLE.search(summary)
    ):
        return "another commitment"
    return summary


def explain_calendar_date_conflict(
    tenant_id: str,
    date_iso: str,
    *,
    caller_name: str | None = None,
    topic: str | None = None,
    timezone: str | None = None,
) -> dict[str, Any] | None:
    events = _calendar_events_for_date(tenant_id, date_iso, timezone=timezone)
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
    alternatives = [
        event
        for event in events
        if event is not subject and "birthday" not in str(event.get("summary") or "").lower()
    ]
    alternatives.sort(
        key=lambda event: (bool(event.get("all_day")), -len(str(event.get("summary") or "")))
    )
    reason = _caller_facing_title(alternatives[0]) if alternatives else None
    return {
        "date": date_iso,
        "related_event_found": subject is not None,
        "reason": reason,
        "source": "tenant_google_calendar",
    }
