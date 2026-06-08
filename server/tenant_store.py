"""Tenant-isolated cloud records for real voicemail-agent onboarding.

The legacy demo writes one local ``owner_config.json`` file. This module is the
production boundary: every onboarded owner gets a separate record, addressed by
an unguessable tenant id and protected by a bearer capability whose plaintext is
returned only once.

Raw voice samples and raw diction replies never enter this store. Gradium keeps
the consented voice sample; we retain only the clone id required for TTS.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger

TYPE_TENANT_AGENT = "tenant_agent"
_DEFAULT_LOCAL_PATH = Path(__file__).parent / "aws_store" / "tenant_agents.json"
_ENV_DIR = Path(__file__).resolve().parent
load_dotenv(_ENV_DIR / ".env", override=True)
load_dotenv(_ENV_DIR / ".env.local", override=True)
_PUBLIC_FIELDS = {
    "record_id",
    "type",
    "tenant_id",
    "created_at",
    "updated_at",
    "activation_status",
    "display_name",
    "owner_phone",
    "owner_email",
    "timezone",
    "agent_phone",
    "agent_phone_source",
    "agent_phone_assigned_at",
    "forwarding_phone",
    "forwarding_phone_assigned_at",
    "routing_mode",
    "voicemail_forwarding_confirmed",
    "voice_clone_name",
    "voice_clone_status",
    "voice_clone_updated_at",
    "gmail_connected_email",
    "gmail_connected_at",
    "calendar_connected_email",
    "calendar_connected_at",
    "diction_profile_phrases",
    "diction_profile_style_notes",
    "diction_profile_lines",
    "diction_profile_updated_at",
}


class TenantStoreUnavailable(RuntimeError):
    """Raised when cloud-only tenant storage cannot be reached."""


class TenantNotFound(LookupError):
    """Raised when a tenant id is missing."""


class TenantAuthError(PermissionError):
    """Raised when a tenant capability token does not match."""


class TenantConflict(RuntimeError):
    """Raised when a unique tenant resource is already assigned."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _record_id(tenant_id: str) -> str:
    return f"tenant-agent:{tenant_id}"


def _token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    stripped = phone.strip()
    digits = "".join(ch for ch in stripped if ch.isdigit())
    return f"+{digits}" if digits else ""


def _cloud_required() -> bool:
    return os.getenv("TENANT_STORE_REQUIRE_CLOUD", "").strip().lower() == "true"


def _table_name() -> str:
    return os.getenv("TENANT_STORE_DDB_TABLE") or os.getenv("PERSIST_DDB_TABLE") or "ff-voicemails"


class _Backend:
    """DynamoDB when available, otherwise a local development fallback."""

    def __init__(self) -> None:
        self.kind = "local"
        self._ddb_table = None
        self._local_path = Path(os.getenv("TENANT_STORE_LOCAL_PATH", str(_DEFAULT_LOCAL_PATH)))
        self._resolve()

    def _resolve(self) -> None:
        if os.getenv("TENANT_STORE_FORCE_LOCAL", "").strip().lower() == "true":
            self._local_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info("tenant store: forced local development file '{}'", self._local_path)
            return
        try:
            import boto3  # noqa: PLC0415

            session = boto3.Session()
            if session.get_credentials() is not None:
                dynamodb: Any = session.resource("dynamodb")
                table = dynamodb.Table(_table_name())
                table.load()
                self._ddb_table = table
                self.kind = "dynamodb"
                logger.info("tenant store: using DynamoDB table '{}'", _table_name())
                return
        except Exception as exc:
            logger.warning("tenant store: DynamoDB unavailable ({})", type(exc).__name__)

        if _cloud_required():
            raise TenantStoreUnavailable("Cloud tenant storage is required but unavailable.")
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("tenant store: using local development file '{}'", self._local_path)

    def put(self, record: dict[str, Any]) -> None:
        if self.kind == "dynamodb":
            self._ddb_table.put_item(Item=record)  # type: ignore[union-attr]
            return
        records = self._read_local()
        records[record["record_id"]] = record
        self._local_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    def get(self, record_id: str) -> dict[str, Any] | None:
        if self.kind == "dynamodb":
            try:
                response = self._ddb_table.get_item(Key={"record_id": record_id})  # type: ignore[union-attr]
            except Exception as exc:
                if "AccessDeniedException" not in str(exc):
                    raise
                logger.warning("tenant store: DynamoDB GetItem denied; falling back to Scan")
                return self._scan_one_by_attr("record_id", record_id)
            item = response.get("Item")
            return dict(item) if item else None
        return self._read_local().get(record_id)

    def _scan_one_by_attr(self, attr_name: str, value: str) -> dict[str, Any] | None:
        from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

        response = self._ddb_table.scan(  # type: ignore[union-attr]
            FilterExpression=Attr(attr_name).eq(value),
            Limit=1,
        )
        items = list(response.get("Items", []))
        while not items and response.get("LastEvaluatedKey"):
            response = self._ddb_table.scan(  # type: ignore[union-attr]
                FilterExpression=Attr(attr_name).eq(value),
                Limit=1,
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items = list(response.get("Items", []))
        return dict(items[0]) if items else None

    def find_by_agent_phone(self, agent_phone: str) -> dict[str, Any] | None:
        normalized = _normalize_phone(agent_phone)
        if not normalized:
            return None
        if self.kind == "dynamodb":
            from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

            response = self._ddb_table.scan(  # type: ignore[union-attr]
                FilterExpression=Attr("type").eq(TYPE_TENANT_AGENT)
                & Attr("agent_phone").eq(normalized)
            )
            items = list(response.get("Items", []))
            while response.get("LastEvaluatedKey"):
                response = self._ddb_table.scan(  # type: ignore[union-attr]
                    FilterExpression=Attr("type").eq(TYPE_TENANT_AGENT)
                    & Attr("agent_phone").eq(normalized),
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))
        else:
            items = list(self._read_local().values())
        for item in items:
            if item.get("type") == TYPE_TENANT_AGENT and item.get("agent_phone") == normalized:
                return dict(item)
        return None

    def find_by_owner_phone(self, owner_phone: str) -> dict[str, Any] | None:
        normalized = _normalize_phone(owner_phone)
        if not normalized:
            return None
        if self.kind == "dynamodb":
            from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

            response = self._ddb_table.scan(  # type: ignore[union-attr]
                FilterExpression=Attr("type").eq(TYPE_TENANT_AGENT)
                & Attr("owner_phone").eq(normalized)
            )
            items = list(response.get("Items", []))
            while response.get("LastEvaluatedKey"):
                response = self._ddb_table.scan(  # type: ignore[union-attr]
                    FilterExpression=Attr("type").eq(TYPE_TENANT_AGENT)
                    & Attr("owner_phone").eq(normalized),
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))
        else:
            items = list(self._read_local().values())
        ready_items = [
            dict(item)
            for item in items
            if item.get("type") == TYPE_TENANT_AGENT
            and item.get("owner_phone") == normalized
            and item.get("forwarding_phone")
        ]
        if ready_items:
            return sorted(ready_items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[0]
        for item in items:
            if item.get("type") == TYPE_TENANT_AGENT and item.get("owner_phone") == normalized:
                return dict(item)
        return None

    def list_tenant_agents(self) -> list[dict[str, Any]]:
        if self.kind == "dynamodb":
            from boto3.dynamodb.conditions import Attr  # noqa: PLC0415

            response = self._ddb_table.scan(  # type: ignore[union-attr]
                FilterExpression=Attr("type").eq(TYPE_TENANT_AGENT)
            )
            items = list(response.get("Items", []))
            while response.get("LastEvaluatedKey"):
                response = self._ddb_table.scan(  # type: ignore[union-attr]
                    FilterExpression=Attr("type").eq(TYPE_TENANT_AGENT),
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                items.extend(response.get("Items", []))
            return [dict(item) for item in items if item.get("type") == TYPE_TENANT_AGENT]
        return [
            dict(item)
            for item in self._read_local().values()
            if item.get("type") == TYPE_TENANT_AGENT
        ]

    def _read_local(self) -> dict[str, dict[str, Any]]:
        if not self._local_path.exists():
            return {}
        try:
            payload = json.loads(self._local_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}


_backend: _Backend | None = None


def _get_backend() -> _Backend:
    global _backend
    if _backend is None:
        _backend = _Backend()
    return _backend


def backend_kind() -> str:
    return _get_backend().kind


def public_agent(record: dict[str, Any]) -> dict[str, Any]:
    """Return fields safe to show to the owner in an onboarding UI."""
    return {key: record.get(key) for key in _PUBLIC_FIELDS if key in record}


def create_agent_session(
    *,
    display_name: str = "",
    owner_phone: str = "",
    owner_email: str = "",
    timezone: str = "America/Los_Angeles",
) -> tuple[dict[str, Any], str]:
    """Create one isolated owner agent and return its one-time capability token."""
    tenant_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    now = _now_iso()
    record = {
        "record_id": _record_id(tenant_id),
        "type": TYPE_TENANT_AGENT,
        "tenant_id": tenant_id,
        "activation_token_sha256": _token_sha256(token),
        "created_at": now,
        "updated_at": now,
        "activation_status": "draft",
        "display_name": " ".join(display_name.split()).strip()[:120],
        "owner_phone": _normalize_phone(owner_phone),
        "owner_email": owner_email.strip()[:320],
        "timezone": timezone.strip()[:100] or "America/Los_Angeles",
        "agent_phone": "",
        "agent_phone_source": "",
        "agent_phone_assigned_at": "",
        "forwarding_phone": "",
        "forwarding_phone_assigned_at": "",
        "routing_mode": "",
        "voicemail_forwarding_confirmed": False,
        "voice_clone_id": "",
        "voice_clone_consent": False,
        "voice_clone_name": "",
        "voice_clone_status": "not_started",
        "voice_clone_updated_at": "",
        "gmail_connected_email": "",
        "gmail_connected_at": "",
        "calendar_connected_email": "",
        "calendar_connected_at": "",
        "diction_profile_phrases": [],
        "diction_profile_style_notes": [],
        "diction_profile_lines": [],
        "diction_profile_updated_at": "",
    }
    _get_backend().put(record)
    return public_agent(record), token


def authenticate(tenant_id: str, token: str) -> dict[str, Any]:
    record = _get_backend().get(_record_id(tenant_id))
    if not record or record.get("type") != TYPE_TENANT_AGENT:
        raise TenantNotFound(tenant_id)
    expected = str(record.get("activation_token_sha256") or "")
    if not token or not hmac.compare_digest(expected, _token_sha256(token)):
        raise TenantAuthError(tenant_id)
    return record


def get_agent(tenant_id: str, token: str) -> dict[str, Any]:
    return public_agent(authenticate(tenant_id, token))


def update_agent(tenant_id: str, token: str, patch: dict[str, Any]) -> dict[str, Any]:
    record = authenticate(tenant_id, token)
    allowed = {
        "display_name",
        "owner_phone",
        "owner_email",
        "timezone",
        "voicemail_forwarding_confirmed",
        "voice_clone_id",
        "voice_clone_consent",
        "voice_clone_name",
        "voice_clone_status",
        "voice_clone_updated_at",
        "diction_profile_phrases",
        "diction_profile_style_notes",
        "diction_profile_lines",
        "diction_profile_updated_at",
    }
    clean = {key: value for key, value in patch.items() if key in allowed}
    if "display_name" in clean:
        clean["display_name"] = " ".join(str(clean["display_name"]).split()).strip()[:120]
    if "owner_phone" in clean:
        clean["owner_phone"] = _normalize_phone(str(clean["owner_phone"]))
    if "owner_email" in clean:
        clean["owner_email"] = str(clean["owner_email"]).strip()[:320]
    if "timezone" in clean:
        clean["timezone"] = str(clean["timezone"]).strip()[:100] or "America/Los_Angeles"
    record.update(clean)
    record["updated_at"] = _now_iso()
    _refresh_activation_status(record)
    _get_backend().put(record)
    return public_agent(record)


def mark_google_connected(tenant_id: str, service: str, email: str) -> dict[str, Any]:
    """Internal OAuth callback update after a tenant connects Google."""
    if service not in {"gmail", "calendar"}:
        raise ValueError(f"Unsupported Google service: {service}")
    record = _get_backend().get(_record_id(tenant_id))
    if not record or record.get("type") != TYPE_TENANT_AGENT:
        raise TenantNotFound(tenant_id)
    record[f"{service}_connected_email"] = email.strip()[:320]
    record[f"{service}_connected_at"] = _now_iso()
    record["updated_at"] = _now_iso()
    _refresh_activation_status(record)
    _get_backend().put(record)
    return public_agent(record)


def mark_google_disconnected(tenant_id: str, service: str, token: str) -> dict[str, Any]:
    """Tenant-authenticated metadata update after disconnecting Google."""
    if service not in {"gmail", "calendar"}:
        raise ValueError(f"Unsupported Google service: {service}")
    record = authenticate(tenant_id, token)
    record[f"{service}_connected_email"] = ""
    record[f"{service}_connected_at"] = ""
    record["updated_at"] = _now_iso()
    _refresh_activation_status(record)
    _get_backend().put(record)
    return public_agent(record)


def assign_agent_phone(tenant_id: str, agent_phone: str, *, source: str = "manual") -> dict[str, Any]:
    record = _get_backend().get(_record_id(tenant_id))
    if not record or record.get("type") != TYPE_TENANT_AGENT:
        raise TenantNotFound(tenant_id)
    normalized = _normalize_phone(agent_phone)
    existing = _get_backend().find_by_agent_phone(normalized)
    if existing and existing.get("tenant_id") != tenant_id:
        raise TenantConflict(f"Agent phone {normalized} is already assigned.")
    record["agent_phone"] = normalized
    record["agent_phone_source"] = " ".join(source.split()).strip()[:40] or "manual"
    record["agent_phone_assigned_at"] = _now_iso()
    record["updated_at"] = _now_iso()
    _refresh_activation_status(record)
    _get_backend().put(record)
    return public_agent(record)


def count_agent_phone_assignments(*, source: str | None = None, since_iso: str | None = None) -> int:
    """Count assigned tenant phones for spend/rate-limit guardrails."""
    count = 0
    for record in _get_backend().list_tenant_agents():
        if not record.get("agent_phone"):
            continue
        if source and record.get("agent_phone_source") != source:
            continue
        if since_iso and str(record.get("agent_phone_assigned_at") or "") < since_iso:
            continue
        count += 1
    return count


def get_agent_by_phone(agent_phone: str | None) -> dict[str, Any] | None:
    if not agent_phone:
        return None
    return _get_backend().find_by_agent_phone(agent_phone)


def get_agent_by_owner_phone(owner_phone: str | None) -> dict[str, Any] | None:
    if not owner_phone:
        return None
    return _get_backend().find_by_owner_phone(owner_phone)


def assign_shared_forwarding_phone(tenant_id: str, forwarding_phone: str) -> dict[str, Any]:
    record = _get_backend().get(_record_id(tenant_id))
    if not record or record.get("type") != TYPE_TENANT_AGENT:
        raise TenantNotFound(tenant_id)
    owner_phone = str(record.get("owner_phone") or "").strip()
    if not owner_phone:
        raise TenantConflict("Owner phone is required for shared forwarding routing.")
    existing = _get_backend().find_by_owner_phone(owner_phone)
    if existing and existing.get("tenant_id") != tenant_id and existing.get("forwarding_phone"):
        raise TenantConflict(f"Owner phone {owner_phone} already has shared forwarding.")
    normalized = _normalize_phone(forwarding_phone)
    if not normalized:
        raise TenantConflict("Forwarding phone is required.")
    record["forwarding_phone"] = normalized
    record["forwarding_phone_assigned_at"] = _now_iso()
    record["routing_mode"] = "shared_forwarded_from"
    record["updated_at"] = _now_iso()
    _refresh_activation_status(record)
    _get_backend().put(record)
    return public_agent(record)


def build_persona_context(record: dict[str, Any]) -> str:
    """Build the bounded per-owner prompt fragment used by the voice runtime."""
    parts: list[str] = []
    name = str(record.get("display_name") or "").strip()
    if name:
        parts.append(f"You are answering on behalf of {name}.")
    timezone = str(record.get("timezone") or "").strip()
    if timezone:
        parts.append(f"Owner timezone: {timezone}.")
    lines = record.get("diction_profile_lines") or []
    if isinstance(lines, list):
        cleaned = [str(line).strip() for line in lines if str(line).strip()]
        if cleaned:
            parts.append("Owner diction profile:\n" + "\n".join(cleaned))
    return " ".join(parts)[:1500]


def _refresh_activation_status(record: dict[str, Any]) -> None:
    if (
        (record.get("agent_phone") or record.get("forwarding_phone"))
        and record.get("voice_clone_status") == "ready"
        and record.get("diction_profile_lines")
    ):
        record["activation_status"] = "ready"
    elif record.get("agent_phone") or record.get("forwarding_phone"):
        record["activation_status"] = "provisioning"
    else:
        record["activation_status"] = "draft"
