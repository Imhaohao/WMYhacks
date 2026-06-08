"""Owner developer/social tool layer for the voice agent.

Wires the owner's full stack into the live voice pipeline by appending tools to
``interfaces.TOOL_REGISTRY`` at import time (same pattern as ``persona_tools.py``):

  * ``check_github``        — recent activity, open PRs, unread notifications
  * ``check_owner_inbox``   — read / search the owner's Gmail (read-side)
  * ``add_reminder``        — add a macOS Reminders item (e.g. "call X back")
  * ``list_reminders``      — list the owner's open reminders

Calendar (read context + tentative booking) and outbound email already live in
``persona_tools.py``; this module completes the set.

Privacy model — two speaker modes:
  * **Assistant mode** (the owner is talking to their own agent): full access.
    True when ``ASSISTANT_MODE`` is truthy, or the inbound number matches
    ``OWNER_PHONE_NUMBER``. Local dev sets ``ASSISTANT_MODE=1`` in .env.local.
  * **Voicemail mode** (a random caller): private reads (github / inbox / list
    reminders) are refused with a safe answer hint. ``add_reminder`` is still
    allowed — a caller asking the agent to remind the owner of something is the
    whole point — but the caller's identity is stamped into the reminder note.

Import this from ``bot-nemotron.py`` next to ``import persona_tools``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from loguru import logger
from pipecat.services.llm_service import FunctionCallParams

import github_client
import google_calendar
import google_gmail
import reminders_client
import tenant_google
from interfaces import TOOL_REGISTRY, CallState

_DENY_HINT = (
    "This is private to the owner. Do not disclose it. Offer to take a message instead."
)


def _owner_session(call_state: CallState) -> bool:
    """True when the live speaker is the owner (assistant mode), not a stranger."""
    vm = call_state.get("voicemail") or {}
    number = _normalize_phone(str(vm.get("caller_number") or ""))
    tenant_runtime = call_state.get("tenant_runtime") or {}
    if tenant_runtime:
        owner = _normalize_phone(str(tenant_runtime.get("owner_phone") or ""))
        # Tenant calls must never inherit the demo ASSISTANT_MODE flag.
        return bool(owner and number and number == owner)
    if os.getenv("ASSISTANT_MODE", "").strip().lower() in ("1", "true", "yes"):
        return True
    owner = _normalize_phone(os.getenv("OWNER_PHONE_NUMBER", "").strip())
    return bool(owner and number and number == owner)


def _normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"+{digits}" if digits else ""


def _tenant_id(call_state: CallState) -> str:
    tenant_runtime = call_state.get("tenant_runtime") or {}
    return str(tenant_runtime.get("tenant_id") or "").strip()


def _tenant_timezone(call_state: CallState) -> str:
    tenant_runtime = call_state.get("tenant_runtime") or {}
    return str(tenant_runtime.get("timezone") or os.getenv("OWNER_TZ") or "America/Los_Angeles").strip()


def _caller_tag(call_state: CallState) -> str:
    """Short 'from <name> (<number>)' tag for stamping caller-requested reminders."""
    snap = call_state.get("caller_snapshot") or {}
    vm = call_state.get("voicemail") or {}
    name = str(snap.get("caller_name") or "").strip()
    number = str(vm.get("caller_number") or "").strip()
    if name and number:
        return f"{name} ({number})"
    return name or number or "an unidentified caller"


# ─── GitHub ──────────────────────────────────────────────────────────────────


async def check_github(
    params: FunctionCallParams,
    call_state: CallState,
    limit: int = 5,
) -> None:
    """Check the owner's recent GitHub activity, open pull requests, and unread
    notifications. Owner-only — use when the owner asks what they're working on,
    whether a PR is open, or if they have GitHub notifications.

    Args:
        limit: Max items per section (recent events / open PRs). Default 5.
    """
    if not _owner_session(call_state):
        await params.result_callback({"authorized": False, "answer_hint": _DENY_HINT})
        return
    data = await asyncio.to_thread(github_client.summary, limit)
    if not data.get("configured"):
        await params.result_callback(
            {
                "authorized": True,
                "connected": False,
                "answer_hint": "GitHub isn't connected yet. " + str(data.get("detail", "")),
            }
        )
        return
    await params.result_callback(
        {
            "authorized": True,
            "connected": True,
            "username": data.get("username"),
            "recent_events": data.get("recent_events"),
            "open_pull_requests": data.get("open_pull_requests"),
            "unread_notifications": data.get("unread_notifications"),
            "answer_hint": (
                "Summarize briefly and conversationally — counts and a couple of "
                "highlights, not a full list. Do not read out URLs."
            ),
        }
    )


# ─── Gmail (read side) ─────────────────────────────────────────────────────────


async def check_owner_inbox(
    params: FunctionCallParams,
    call_state: CallState,
    query: str | None = None,
    max_results: int = 5,
) -> None:
    """Read or search the owner's Gmail inbox. Owner-only — use when the owner
    asks about recent email, unread messages, or whether someone wrote to them.

    Args:
        query: Optional Gmail search string (e.g. "is:unread", "from:alice",
            "newer_than:2d"). Omit for the most recent inbox messages.
        max_results: Max messages to return. Default 5.
    """
    if not _owner_session(call_state):
        await params.result_callback({"authorized": False, "answer_hint": _DENY_HINT})
        return
    tenant_id = _tenant_id(call_state)
    if tenant_id:
        if not tenant_google.is_connected(tenant_id, tenant_google.GMAIL):
            await params.result_callback(
                {
                    "authorized": True,
                    "connected": False,
                    "answer_hint": "Gmail isn't connected. Ask the owner to connect it in setup.",
                }
            )
            return
        messages = await asyncio.to_thread(
            tenant_google.list_recent_gmail,
            tenant_id,
            max_results,
            (query or "").strip(),
        )
        await params.result_callback(
            {
                "authorized": True,
                "connected": True,
                "count": len(messages),
                "messages": messages,
                "source": "tenant_google_gmail",
                "answer_hint": (
                    "Summarize who emailed about what, newest first. Keep it short; "
                    "do not read full email bodies aloud unless asked."
                ),
            }
        )
        return
    if not google_gmail.is_connected():
        await params.result_callback(
            {
                "authorized": True,
                "connected": False,
                "answer_hint": "Gmail isn't connected. Ask the owner to connect it in setup.",
            }
        )
        return
    messages = await asyncio.to_thread(
        google_gmail.list_recent, max_results, (query or "").strip()
    )
    read_error = google_gmail.last_read_error()
    if not messages and read_error:
        await params.result_callback(
            {
                "authorized": True,
                "connected": True,
                "count": 0,
                "error": read_error,
                "answer_hint": "Inbox read isn't working right now; say so briefly and offer to take a message.",
            }
        )
        return
    await params.result_callback(
        {
            "authorized": True,
            "connected": True,
            "count": len(messages),
            "messages": messages,
            "answer_hint": (
                "Summarize who emailed about what, newest first. Keep it short; "
                "do not read full email bodies aloud unless asked."
            ),
        }
    )


# ─── macOS Reminders ───────────────────────────────────────────────────────────


async def add_reminder(
    params: FunctionCallParams,
    call_state: CallState,
    title: str,
    notes: str | None = None,
) -> None:
    """Add an item to the owner's macOS Reminders. Use this when the owner asks to
    be reminded of something, OR when a caller asks you to make sure the owner
    remembers to do something (e.g. "tell him to call me back").

    Args:
        title: Short reminder text, e.g. "Call Sarah back about the contract".
        notes: Optional extra detail.
    """
    title = (title or "").strip()
    if not title:
        await params.result_callback(
            {"ok": False, "answer_hint": "Ask what the reminder should say."}
        )
        return
    final_notes = notes
    # Stamp caller-requested reminders so the owner knows the source.
    if not _owner_session(call_state):
        tag = f"Requested by {_caller_tag(call_state)}."
        final_notes = f"{notes}\n{tag}" if notes else tag
    result = await asyncio.to_thread(reminders_client.add_reminder, title, final_notes)
    result["answer_hint"] = (
        f"Confirm you've added the reminder: '{title}'."
        if result.get("ok")
        else "Could not add the reminder; offer to take a message instead."
    )
    await params.result_callback(result)


async def list_reminders(
    params: FunctionCallParams,
    call_state: CallState,
    limit: int = 10,
) -> None:
    """List the owner's open reminders. Owner-only — use when the owner asks
    what's on their reminders / to-do list.

    Args:
        limit: Max reminders to return. Default 10.
    """
    if not _owner_session(call_state):
        await params.result_callback({"authorized": False, "answer_hint": _DENY_HINT})
        return
    result = await asyncio.to_thread(reminders_client.list_reminders, limit, False)
    result["authorized"] = True
    result["answer_hint"] = (
        "Read back the open reminders briefly."
        if result.get("ok")
        else "Reminders aren't reachable right now; say so briefly."
    )
    await params.result_callback(result)


# ─── Calendar (owner-facing read) ──────────────────────────────────────────────


async def check_owner_calendar(
    params: FunctionCallParams,
    call_state: CallState,
    date: str | None = None,
) -> None:
    """Check the OWNER's own calendar — what's scheduled on a day and whether
    they're free. Use this when the owner asks about THEIR calendar: "what's on
    my calendar", "am I free Thursday", "what do I have today/tomorrow".

    (For a CALLER asking why the owner missed something, use
    get_calendar_context_for_caller instead — that one is privacy-gated.)

    Args:
        date: Local date as YYYY-MM-DD. Defaults to today in the owner's timezone.
    """
    if not _owner_session(call_state):
        await params.result_callback({"authorized": False, "answer_hint": _DENY_HINT})
        return
    import datetime as _dt
    from zoneinfo import ZoneInfo

    tz = _tenant_timezone(call_state)
    query_date = (date or "").strip() or _dt.datetime.now(ZoneInfo(tz)).date().isoformat()
    tenant_id = _tenant_id(call_state)
    if tenant_id:
        events = await asyncio.to_thread(
            tenant_google.list_calendar_events_for_date,
            tenant_id,
            query_date,
            timezone=tz,
        )
        if events is None:
            await params.result_callback(
                {
                    "authorized": True,
                    "connected": False,
                    "answer_hint": "Calendar read isn't available right now; say so briefly.",
                }
            )
            return
        simplified = [
            {
                "title": e.get("summary"),
                "start": e.get("start"),
                "end": e.get("end"),
                "all_day": e.get("all_day"),
            }
            for e in events
        ]
        await params.result_callback(
            {
                "authorized": True,
                "connected": True,
                "date": query_date,
                "event_count": len(simplified),
                "events": simplified,
                "source": "tenant_google_calendar",
                "answer_hint": (
                    "Tell me what's on my calendar for that day, naturally and briefly. "
                    "If there are no events, say the day looks open."
                ),
            }
        )
        return
    events = await asyncio.to_thread(
        google_calendar.list_events_for_date, query_date, timezone=tz
    )
    if events is None:
        await params.result_callback(
            {
                "authorized": True,
                "connected": False,
                "error": getattr(google_calendar, "_last_api_error", None),
                "answer_hint": "Calendar read isn't available right now; say so briefly.",
            }
        )
        return
    simplified = [
        {
            "title": e.get("summary"),
            "start": e.get("start"),
            "end": e.get("end"),
            "all_day": e.get("all_day"),
        }
        for e in events
    ]
    await params.result_callback(
        {
            "authorized": True,
            "connected": True,
            "date": query_date,
            "event_count": len(simplified),
            "events": simplified,
            "answer_hint": (
                "Tell me what's on my calendar for that day, naturally and briefly. "
                "If there are no events, say the day looks open."
            ),
        }
    )


# ─── Registry append (runs at import time) ────────────────────────────────────

TOOL_REGISTRY.extend(
    [
        check_github,
        check_owner_inbox,
        check_owner_calendar,
        add_reminder,
        list_reminders,
    ]
)

logger.info(
    "owner_tools loaded — registered 5 tools "
    f"(github={github_client.is_configured()}, gmail_read={google_gmail.is_connected()}, "
    f"calendar={google_calendar.is_connected()}, reminders={reminders_client.is_available()})"
)
