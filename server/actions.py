#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Action layer: email the owner + book a tentative callback slot (Person 3, Part B).

Two Pipecat tools the voicemail bot can call once a voicemail is captured:

  * ``send_owner_email``   — emails the owner the voicemail summary.
  * ``book_callback_slot`` — drops a *tentative* callback event on the owner's
    calendar when the caller requested a time and the owner is free.

Architecture / why an outbox
-----------------------------
The running bot is a plain Python process; it cannot call the agent-session
Gmail/Calendar MCP connectors directly. So every action is recorded two ways:

  1. into ``call_state["actions"]`` (so the final summary + persisted record
     show "actions taken"), and
  2. appended to an **outbox** JSONL (``server/outbox/actions.jsonl``) as a
     clean, MCP-ready request.

An action is then *fulfilled* by either:
  * an inline backend, when creds exist (e.g. Gmail SMTP for email via
    ``GMAIL_APP_PASSWORD``), or
  * the **bridge** (``action_bridge.py`` / a human / the agent on stage) which
    drains the outbox and fires the Gmail/Calendar MCP tools.

Everything degrades gracefully (log + skip, status ``"queued"`` or
``"skipped"``) so a missing connector never hard-fails the demo (task 7).

Guardrail (task 5/6): outward actions target the **owner's own** email and the
**owner's own** calendar only. The recipient is forced to ``OWNER_EMAIL`` (or
the configured default) regardless of anything the caller says.
"""

from __future__ import annotations

import json
import os
import smtplib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from loguru import logger

_DEFAULT_OUTBOX = Path(__file__).parent / "outbox" / "actions.jsonl"

# Owner identity for the demo. Everything outward goes here and only here.
DEFAULT_OWNER_EMAIL = "imzihaoi@gmail.com"


def owner_email() -> str:
    return os.getenv("OWNER_EMAIL", DEFAULT_OWNER_EMAIL)


def _outbox_path() -> Path:
    return Path(os.getenv("PERSIST_OUTBOX_PATH", str(_DEFAULT_OUTBOX)))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _append_outbox(action: dict[str, Any]) -> str:
    path = _outbox_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(action, default=str) + "\n")
        return f"outbox:{path}"
    except Exception as e:
        logger.warning(f"actions: outbox append failed ({type(e).__name__})")
        return "outbox:unwritten"


def _record_action(call_state: dict[str, Any], action: dict[str, Any]) -> None:
    call_state.setdefault("actions", []).append(action)


# --- Summary building -------------------------------------------------------

def build_summary_text(call_state: dict[str, Any]) -> str:
    """Compose a plain-text voicemail summary from the frozen call_state.

    Pulls the structured voicemail fields (P1) and the caller snapshot (P3).
    Defensive against either being absent so it works mid-integration."""
    lines: list[str] = ["New voicemail for you:", ""]

    vm = call_state.get("voicemail") or {}
    # P1's structured fields — render whatever is present, in a stable order.
    for key in ("caller_name", "reason", "urgency", "callback_number", "callback_preference",
                "best_time", "message"):
        val = vm.get(key)
        if val:
            lines.append(f"  {key.replace('_', ' ').title()}: {val}")

    try:
        from caller_snapshot import format_snapshot_summary

        snap = call_state.get("caller_snapshot")
        if snap:
            lines.append("")
            lines.append(format_snapshot_summary(snap))
    except Exception:  # never let summary building break an action
        pass

    return "\n".join(lines).strip() or "New voicemail (no structured details captured)."


# --- Email backend ----------------------------------------------------------

def _send_via_smtp(to_addr: str, subject: str, body: str) -> bool:
    """Send through Gmail SMTP using an app password. Returns True on success.

    Enabled only when both ``OWNER_EMAIL`` and ``GMAIL_APP_PASSWORD`` are set.
    Any failure returns False (caller then queues to the outbox)."""
    app_pw = os.getenv("GMAIL_APP_PASSWORD")
    sender = os.getenv("GMAIL_SENDER", to_addr)
    if not app_pw:
        return False
    try:
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
            s.starttls()
            s.login(sender, app_pw)
            s.send_message(msg)
        return True
    except Exception as e:
        logger.warning(f"actions: SMTP send failed ({type(e).__name__}); will queue to outbox")
        return False


# --- Calendar helpers -------------------------------------------------------

def _resolve_slot(
    start_iso: str | None, end_iso: str | None, requested_time: str | None
) -> tuple[str | None, str | None]:
    """Best-effort: if a start is given without an end, default to a 30-minute
    slot. We do NOT hard-parse vague phrases ("before 4pm") here — those ride
    along as ``requested_time`` for the owner/bridge to confirm."""
    if start_iso and not end_iso:
        try:
            start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            end_iso = (start + timedelta(minutes=30)).isoformat()
        except ValueError:
            pass
    return start_iso, end_iso


# --- Tool factory -----------------------------------------------------------

def make_action_tools(
    call_state: dict[str, Any],
    persist_hook: Callable[..., Any] | None = None,
    free_busy_check: Callable[[str | None, str | None], bool | None] | None = None,
) -> list[Callable[..., Any]]:
    """Build the Pipecat action tools as closures over ``call_state``.

    Args:
        call_state: P1's frozen per-call state.
        persist_hook: optional; unused directly here (the voicemail record
            persisted at end-of-call already embeds ``call_state["actions"]``).
            Accepted for symmetry / future inline persistence.
        free_busy_check: optional callable ``(start_iso, end_iso) -> bool|None``
            from P2's calendar read side. ``True`` = owner free, ``False`` =
            busy, ``None`` = unknown. When busy we still record the request but
            mark it ``needs_reschedule`` rather than booking over a conflict.

    Returns ``[send_owner_email, book_callback_slot]``.
    """
    from pipecat.services.llm_service import FunctionCallParams  # local import

    async def send_owner_email(
        params: FunctionCallParams,
        subject: str | None = None,
    ) -> None:
        """Email the shop owner this voicemail's summary. Call this once you've
        captured the caller's name, reason, and how to reach them back. The
        email always goes to the owner's own inbox.

        Args:
            subject: Optional subject line. Defaults to a sensible one built
                from the caller's name and reason.
        """
        to_addr = owner_email()
        snap = call_state.get("caller_snapshot") or {}
        vm = call_state.get("voicemail") or {}
        who = snap.get("caller_name") or vm.get("caller_name") or "a caller"
        why = snap.get("reason") or vm.get("reason") or "a message"
        subj = subject or f"Voicemail from {who} — {why}"
        body = build_summary_text(call_state)

        sent = _send_via_smtp(to_addr, subj, body)
        action = {
            "action_id": uuid.uuid4().hex,
            "type": "email",
            "channel": "gmail",
            "to": to_addr,
            "subject": subj,
            "body": body,
            "status": "sent" if sent else "queued",
            "fulfilled_by": "smtp" if sent else "bridge",
            "timestamp": _now_iso(),
        }
        if not sent:
            action["outbox"] = _append_outbox({"action": "send_email", **action})
        _record_action(call_state, action)
        logger.info(f"send_owner_email -> {to_addr} status={action['status']}")
        await params.result_callback(
            {
                "ok": True,
                "status": action["status"],
                "to": to_addr,
                "note": (
                    "Emailed the owner." if sent
                    else "Queued the email for the owner (will send via the connector)."
                ),
            }
        )

    async def book_callback_slot(
        params: FunctionCallParams,
        start_iso: str | None = None,
        end_iso: str | None = None,
        requested_time: str | None = None,
        title: str | None = None,
    ) -> None:
        """Put a TENTATIVE callback event on the owner's calendar when the
        caller has asked for a specific callback time. Use this only after the
        caller requests a time/window to be reached.

        Args:
            start_iso: Callback start time as ISO-8601 (e.g.
                "2026-05-30T15:30:00-07:00") if you can resolve one from the
                caller's words and today's date. Optional.
            end_iso: Callback end time as ISO-8601. Optional; defaults to 30
                minutes after start.
            requested_time: The caller's own words for when to call back (e.g.
                "before 4pm today", "tomorrow morning"). Always include this.
            title: Optional event title. Defaults to a callback title with the
                caller's name.
        """
        snap = call_state.get("caller_snapshot") or {}
        who = snap.get("caller_name") or "caller"
        start_iso, end_iso = _resolve_slot(start_iso, end_iso, requested_time)

        free = free_busy_check(start_iso, end_iso) if free_busy_check else None
        if free is False:
            status, note = "needs_reschedule", "Owner is busy then — flagged to reschedule."
        else:
            status, note = "queued", "Tentative callback slot queued for the owner's calendar."

        event = {
            "action_id": uuid.uuid4().hex,
            "type": "calendar",
            "channel": "gcal",
            "calendar": owner_email(),
            "title": title or f"Callback: {who}",
            "start_iso": start_iso,
            "end_iso": end_iso,
            "requested_time": requested_time,
            "tentative": True,
            "owner_free": free,
            "description": build_summary_text(call_state),
            "status": status,
            "fulfilled_by": "bridge",
            "timestamp": _now_iso(),
        }
        event["outbox"] = _append_outbox({"action": "create_event", **event})
        _record_action(call_state, event)
        logger.info(
            f"book_callback_slot title='{event['title']}' start={start_iso} "
            f"free={free} status={status}"
        )
        await params.result_callback(
            {
                "ok": True,
                "status": status,
                "tentative": True,
                "when": requested_time or start_iso,
                "note": note,
            }
        )

    return [send_owner_email, book_callback_slot]
