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
import re
import smtplib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

_DEFAULT_OUTBOX = Path(__file__).parent / "outbox" / "actions.jsonl"

# Owner identity for the demo. Everything outward goes here and only here.
DEFAULT_OWNER_EMAIL = "imzihaoi@gmail.com"


def owner_email() -> str:
    env_email = os.getenv("OWNER_EMAIL", "").strip()
    if env_email:
        return env_email
    try:
        import owner_config

        cfg_email = str(owner_config.load_owner_config().get("owner_email") or "").strip()
        if cfg_email:
            return cfg_email
    except Exception:
        pass
    return DEFAULT_OWNER_EMAIL


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
    msg_candidate = vm.get("message")
    msg = msg_candidate if isinstance(msg_candidate, dict) else vm
    # P1's structured fields — render whatever is present, in a stable order.
    for key in (
        "caller_name",
        "reason",
        "urgency",
        "callback_number",
        "callback_preference",
        "callback_preferred_time",
        "best_time",
    ):
        val = msg.get(key)
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


def _send_via_gmail_oauth(to_addr: str, subject: str, body: str) -> dict[str, Any] | None:
    """Send through the owner's connected Gmail OAuth account, if available."""
    try:
        import google_gmail

        sender = os.getenv("GMAIL_SENDER", to_addr).strip() or to_addr
        return google_gmail.send_email(to_addr, subject, body, sender=sender)
    except Exception as e:
        logger.warning(f"actions: Gmail OAuth send failed ({type(e).__name__}); will fall back")
        return None


def _send_via_resend(to_addr: str, subject: str, body: str) -> bool:
    """Send through Resend's HTTPS API. Returns True on success."""
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        return False
    from_addr = os.getenv("EMAIL_FROM", "onboarding@resend.dev").strip()
    payload = json.dumps(
        {"from": from_addr, "to": [to_addr], "subject": subject, "text": body}
    ).encode("utf-8")
    req = urllib_request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:200]
        logger.warning(f"actions: Resend send failed ({exc.code}): {detail}")
        return False
    except Exception as exc:
        logger.warning(f"actions: Resend send failed ({type(exc).__name__})")
        return False


def _voicemail_message(call_state: dict[str, Any]) -> dict[str, Any]:
    """Return the structured capture dict, whether nested or flat on voicemail."""
    vm = call_state.get("voicemail") or {}
    msg = vm.get("message")
    if isinstance(msg, dict):
        return msg
    return vm if isinstance(vm, dict) else {}


def _hydrate_message_from_snapshot(call_state: dict[str, Any]) -> dict[str, Any]:
    """Copy snapshot fields into voicemail.message when P1 capture_* was skipped."""
    vm = call_state.setdefault("voicemail", {})
    if not isinstance(vm.get("message"), dict):
        vm["message"] = {
            k: v
            for k, v in vm.items()
            if k not in ("message", "summary", "transcript", "email_sent", "sms_sent", "action_items", "callback_slot", "caller_number", "duration_seconds", "recorded_at")
            and v not in (None, "", [], {})
        }
    msg = vm.setdefault("message", {})
    snap = call_state.get("caller_snapshot") or {}
    for key in ("caller_name", "reason"):
        if not msg.get(key) and snap.get(key):
            msg[key] = snap[key]
    if not msg.get("urgency") and snap.get("urgency"):
        msg["urgency"] = snap["urgency"]
    return msg


def _has_email_content(call_state: dict[str, Any]) -> bool:
    msg = _hydrate_message_from_snapshot(call_state)
    flat = _voicemail_message(call_state)
    snap = call_state.get("caller_snapshot") or {}
    return bool(
        msg.get("reason")
        or msg.get("caller_name")
        or flat.get("reason")
        or flat.get("caller_name")
        or snap.get("reason")
        or snap.get("caller_name")
    )


def deliver_owner_email(
    call_state: dict[str, Any],
    subject: str | None = None,
) -> dict[str, Any]:
    """Best-effort owner email: Gmail OAuth → SMTP → Resend → outbox queue.

    Idempotent per call via ``voicemail.email_sent``. Safe to call from
    ``finish_voicemail``, the ``send_owner_email`` tool, or the disconnect
    fallback when the LLM ends the call without firing those tools.
    """
    vm = call_state.setdefault("voicemail", {})
    if vm.get("email_sent"):
        return {"status": "already_sent", "to": owner_email()}

    if not _has_email_content(call_state):
        return {"status": "skipped", "reason": "no caller content captured"}

    msg = vm.get("message") or {}
    flat = _voicemail_message(call_state)
    snap = call_state.get("caller_snapshot") or {}
    who = msg.get("caller_name") or flat.get("caller_name") or snap.get("caller_name") or "a caller"
    why = msg.get("reason") or flat.get("reason") or snap.get("reason") or "a message"
    subj = subject or f"Voicemail from {who} — {why}"
    body = (vm.get("summary") or "").strip() or build_summary_text(call_state)
    to_addr = owner_email()

    gmail_sent = _send_via_gmail_oauth(to_addr, subj, body)
    sent_by = "google_gmail" if gmail_sent else None
    sent = bool(gmail_sent)
    if not sent:
        sent = _send_via_smtp(to_addr, subj, body)
        sent_by = "smtp" if sent else sent_by
    if not sent:
        sent = _send_via_resend(to_addr, subj, body)
        sent_by = "resend" if sent else sent_by

    action: dict[str, Any] = {
        "action_id": uuid.uuid4().hex,
        "type": "email",
        "channel": "gmail",
        "to": to_addr,
        "subject": subj,
        "body": body,
        "status": "sent" if sent else "queued",
        "fulfilled_by": sent_by or "bridge",
        "timestamp": _now_iso(),
    }
    if gmail_sent:
        action["gmail_message_id"] = gmail_sent.get("id")
        action["gmail_thread_id"] = gmail_sent.get("threadId")
    if not sent:
        action["outbox"] = _append_outbox({"action": "send_email", **action})
    _record_action(call_state, action)
    if sent:
        vm["email_sent"] = True
    logger.info(f"deliver_owner_email -> {to_addr} status={action['status']}")
    return action


# --- Calendar helpers -------------------------------------------------------

def _owner_timezone() -> ZoneInfo:
    try:
        tz = os.getenv("OWNER_TZ", "").strip()
        if not tz:
            try:
                import owner_config

                tz = str(owner_config.load_owner_config().get("timezone") or "").strip()
            except Exception:
                tz = ""
        return ZoneInfo(tz or "America/Los_Angeles")
    except ZoneInfoNotFoundError:
        logger.warning("actions: invalid OWNER_TZ; falling back to UTC")
        return ZoneInfo("UTC")


def _call_base_time(call_state: dict[str, Any], tz: ZoneInfo) -> datetime:
    recorded_at = (call_state.get("voicemail") or {}).get("recorded_at")
    if recorded_at:
        try:
            parsed = datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(tz)
        except ValueError:
            pass
    return datetime.now(tz)


def _parse_clock(text: str) -> tuple[str, int, int] | None:
    match = re.search(
        r"\b(?:(before|by|after|around|at)\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        text,
    )
    if not match:
        return None
    relation = match.group(1) or "at"
    hour = int(match.group(2))
    minute = int(match.group(3) or "0")
    meridiem = match.group(4)
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem is None and 1 <= hour <= 7:
        # Callback windows without am/pm are usually business-hour afternoons.
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return relation, hour, minute


def _resolve_requested_time(
    requested_time: str | None,
    call_state: dict[str, Any],
) -> tuple[str | None, str | None]:
    if not requested_time:
        return None, None

    text = requested_time.lower().strip()
    tz = _owner_timezone()
    base = _call_base_time(call_state, tz)
    day = base.date()
    explicit_today = "today" in text

    if "tomorrow" in text:
        day = day + timedelta(days=1)

    clock = _parse_clock(text)
    if clock:
        relation, hour, minute = clock
        anchor = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(
            hour=hour,
            minute=minute,
        )
        if relation in {"before", "by"}:
            start = anchor - timedelta(minutes=30)
            end = anchor
        else:
            start = anchor
            end = anchor + timedelta(minutes=30)
    elif "morning" in text:
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(hour=9)
        end = start + timedelta(minutes=30)
    elif "afternoon" in text:
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(hour=13)
        end = start + timedelta(minutes=30)
    elif "evening" in text:
        start = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(hour=17)
        end = start + timedelta(minutes=30)
    else:
        return None, None

    if start <= base and "tomorrow" not in text and not explicit_today:
        start += timedelta(days=1)
        end += timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _resolve_slot(
    start_iso: str | None,
    end_iso: str | None,
    requested_time: str | None,
    call_state: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Best-effort: if a start is given without an end, default to a 30-minute
    slot. If only a simple requested_time phrase is present, resolve common demo
    windows like "before 4pm today" and "tomorrow morning" in OWNER_TZ."""
    if start_iso and not end_iso:
        try:
            start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            end_iso = (start + timedelta(minutes=30)).isoformat()
        except ValueError:
            pass
    if not start_iso:
        start_iso, end_iso = _resolve_requested_time(requested_time, call_state)
    return start_iso, end_iso


# --- Tool factory -----------------------------------------------------------

def make_action_tools(
    call_state: dict[str, Any],
    persist_hook: Callable[..., Any] | None = None,
    free_busy_check: Callable[[str | None, str | None], bool | None] | None = None,
    create_event: Callable[..., dict[str, Any] | None] | None = None,
) -> list[Callable[..., Any]]:
    """Build the Pipecat action tools as closures over ``call_state``.

    Args:
        call_state: P1's frozen per-call state.
        persist_hook: optional; unused directly here (the voicemail record
            persisted at end-of-call already embeds ``call_state["actions"]``).
            Accepted for symmetry / future inline persistence.
        free_busy_check: optional callable ``(start_iso, end_iso) -> bool|None``
            from the calendar read side. ``True`` = owner free, ``False`` =
            busy, ``None`` = unknown. When busy we still record the request but
            mark it ``needs_reschedule`` rather than booking over a conflict.
        create_event: optional callable
            ``(summary, start_iso, end_iso, description, timezone) -> {"id",
            "htmlLink"} | None`` that creates the event directly (e.g. the
            connected Google Calendar). When it returns a link the event is
            booked for real (status ``"booked"``); otherwise we fall back to
            queueing the request to the outbox bridge.

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
        action = deliver_owner_email(call_state, subject=subject)
        to_addr = action.get("to") or owner_email()
        sent = action.get("status") == "sent"
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
        start_iso, end_iso = _resolve_slot(start_iso, end_iso, requested_time, call_state)

        free = free_busy_check(start_iso, end_iso) if free_busy_check else None
        event_title = title or f"Callback: {who}"
        description = build_summary_text(call_state)

        if free is False:
            status, note = "needs_reschedule", "Owner is busy then — flagged to reschedule."
        else:
            status, note = "queued", "Tentative callback slot queued for the owner's calendar."

        event = {
            "action_id": uuid.uuid4().hex,
            "type": "calendar",
            "channel": "gcal",
            "calendar": owner_email(),
            "title": event_title,
            "start_iso": start_iso,
            "end_iso": end_iso,
            "requested_time": requested_time,
            "tentative": True,
            "owner_free": free,
            "description": description,
            "status": status,
            "fulfilled_by": "bridge",
            "timestamp": _now_iso(),
        }

        # Real booking path: if a connected calendar is available and the owner
        # isn't already busy, create the event directly instead of queueing.
        booked = None
        if create_event and free is not False:
            try:
                booked = create_event(
                    event_title,
                    start_iso,
                    end_iso,
                    description,
                    _owner_timezone().key,
                )
            except Exception as e:  # never let a calendar hiccup break the call
                logger.warning(f"actions: create_event hook failed ({type(e).__name__})")
                booked = None

        if booked and booked.get("htmlLink"):
            event["status"] = status = "booked"
            event["fulfilled_by"] = "google_calendar"
            event["event_id"] = booked.get("id")
            event["html_link"] = booked.get("htmlLink")
            note = "Tentative callback event added to your Google Calendar."
        else:
            # Fall back to the outbox bridge when no live calendar is connected.
            event["outbox"] = _append_outbox({"action": "create_event", **event})

        _record_action(call_state, event)
        logger.info(
            f"book_callback_slot title='{event['title']}' start={start_iso} "
            f"free={free} status={event['status']}"
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
