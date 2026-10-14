"""Owner-facing view of a screened call: the text message and the inbox entry."""

from __future__ import annotations

from typing import Any

import persistence

OWNER_TEXT_LIMIT = 320  # two SMS segments
INBOX_LIMIT = 25


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _callback_sentence(message: dict[str, Any], caller_number: str) -> str:
    if not message.get("wants_callback"):
        return ""
    number = _clean(message.get("callback_number"))
    when = _clean(message.get("callback_preferred_time"))
    target = f" at {number}" if number and number != caller_number else ""
    timing = f", {when}" if when else ""
    return f" Wants a call back{target}{timing}."


def owner_text(message: dict[str, Any], caller_number: str) -> str:
    """One plain sentence or two telling the owner who called and why."""
    name = _clean(message.get("caller_name")) or "Someone"
    number = _clean(caller_number)
    reason = _clean(message.get("reason")).rstrip(".")
    who = f"{name} ({number})" if number else name
    about = f" about {reason}" if reason else " but did not say why"
    prefix = "Gotchu, urgent:" if message.get("urgency") == "urgent" else "Gotchu:"
    text = f"{prefix} {who} called{about}.{_callback_sentence(message, number)}"
    if len(text) <= OWNER_TEXT_LIMIT:
        return text
    return text[: OWNER_TEXT_LIMIT - 3].rstrip() + "..."


def inbox_entry(record: dict[str, Any]) -> dict[str, Any]:
    voicemail = record.get("voicemail") or {}
    message = voicemail.get("message") or {}
    caller_number = _clean(voicemail.get("caller_number") or record.get("caller_number"))
    return {
        "id": record.get("record_id"),
        "received_at": voicemail.get("recorded_at") or record.get("timestamp"),
        "duration_seconds": voicemail.get("duration_seconds") or 0,
        "caller_name": _clean(message.get("caller_name")),
        "caller_number": caller_number,
        "reason": _clean(message.get("reason")),
        "urgency": message.get("urgency") or "normal",
        "wants_callback": bool(message.get("wants_callback")),
        "callback_number": _clean(message.get("callback_number")),
        "callback_time": _clean(message.get("callback_preferred_time")),
        "caller_words": str(voicemail.get("transcript") or "").strip(),
        "owner_text": owner_text(message, caller_number),
        "texted": bool(voicemail.get("sms_sent")),
    }


def list_for_tenant(tenant_id: str, limit: int = INBOX_LIMIT) -> list[dict[str, Any]]:
    """Newest-first screened calls that belong to one tenant."""
    records = persistence.list_records("voicemail")
    owned = [r for r in records if r.get("owner_context_tag") == tenant_id]
    return [inbox_entry(r) for r in owned[:limit]]
