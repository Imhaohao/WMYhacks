"""P3 integration shim — the single import bot-nemotron.py needs.

Importing this module:

  1. Appends P3's tools to ``TOOL_REGISTRY`` in interfaces.py:
       • update_caller_snapshot   — live tone / persona detection
       • lookup_persona           — caller-number → known-persona check (stub)
       • send_owner_email         — Gmail email of the voicemail summary
       • book_callback_slot       — tentative Calendar event for callback

  2. Exposes ``on_call_finished(call_state)`` for P1's ``on_client_disconnected``
     handler: persists voicemail + snapshot + actions to DynamoDB / S3 / local
     fallback, and returns the persisted record id.

  3. Exposes ``calendar_free_busy_check`` as the P2 coordination seam — P2 sets
     ``persona_tools.calendar_free_busy_check = <fn>`` to plug their read-side
     availability check into our calendar booking tool.

  4. Exposes ``make_transcript_tone_processor(call_state)`` — a passthrough
     Pipecat processor P1 inserts between ``stt`` and the user aggregator so
     caller tone is inferred from final transcripts without an LLM round-trip.

All tool functions follow P1's signature convention so the ``bind_call_state``
wrapper in ``bot-nemotron.py`` unwraps them automatically:

    async def my_tool(params: FunctionCallParams, call_state: CallState, …) -> None
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from pipecat.services.llm_service import FunctionCallParams

import actions
import caller_snapshot as cs
import contacts
import google_calendar
import persistence
from interfaces import TOOL_REGISTRY, CallState

# ─── Calendar read side ──────────────────────────────────────────────────────
#
# Defaults to the owner's connected Google Calendar (OAuth via the setup wizard).
# When no calendar is connected, ``google_calendar.is_free`` returns None
# ("unknown") and booking falls back to the outbox bridge — so this is always
# safe. A teammate can still override the attribute at import time to plug a
# different availability source:
#
#     import persona_tools
#     persona_tools.calendar_free_busy_check = my_free_busy_check
#
# Signature: ``(start_iso: str | None, end_iso: str | None) -> bool | None``
# Return True if owner is free, False if busy, None if unknown.

calendar_free_busy_check: Callable[[str | None, str | None], bool | None] | None = (
    google_calendar.is_free
)

_ADAPTATION_ROLE = "system"
_ADAPTATION_PREFIX = "CALLER ADAPTATION:"


def sync_voicemail_to_snapshot(call_state: Any) -> dict[str, Any]:
    """Mirror P1's structured capture fields into P3's live caller snapshot.

    P1's capture_* tools are the source of truth for the voicemail payload.
    This helper keeps the richer P3 snapshot in lockstep so summaries, email,
    persistence, and live adaptation all read one coherent per-call state.
    """
    snap: dict[str, Any] = call_state["caller_snapshot"]  # type: ignore[assignment]
    voicemail = call_state.get("voicemail") or {}
    msg = voicemail.get("message") or {}

    urgency_map = {
        "urgent": "high",
        "high": "high",
        "emergency": "emergency",
        "normal": "normal",
        "low": "low",
        "can wait": "low",
        "can_wait": "low",
    }
    raw_urgency = str(msg.get("urgency") or "").lower().strip()
    urgency = urgency_map.get(raw_urgency) if raw_urgency else None

    callback_preference: str | None = None
    if "wants_callback" in msg:
        if msg.get("wants_callback"):
            callback_preference = "call back"
            if msg.get("callback_number"):
                callback_preference = f"call back at {msg['callback_number']}"
        else:
            callback_preference = "no callback requested"

    cs.apply_snapshot_update(
        snap,
        caller_name=msg.get("caller_name"),
        reason=msg.get("reason"),
        urgency=urgency,
        callback_preference=callback_preference,
        best_time=msg.get("callback_preferred_time"),
    )
    cs.normalize_to_contract(snap)
    return snap


def refresh_live_style_directive(call_state: Any, context: Any) -> bool:
    """Upsert the current caller-adaptation directive into an LLMContext.

    Returns True when the context changed. The helper intentionally talks to
    the tiny LLMContext surface used by Pipecat (get_messages/set_messages), so
    it is easy to unit-test with a fake context.
    """
    directive = cs.live_style_directive(call_state)
    messages = list(context.get_messages())

    kept: list[Any] = []
    previous: str | None = None
    for message in messages:
        if (
            isinstance(message, dict)
            and message.get("role") == _ADAPTATION_ROLE
            and str(message.get("content") or "").startswith(_ADAPTATION_PREFIX)
        ):
            previous = str(message.get("content") or "")
            continue
        kept.append(message)

    if previous == directive:
        return False

    kept.append({"role": _ADAPTATION_ROLE, "content": directive})
    context.set_messages(kept)
    return True


# ─── Snapshot tools ──────────────────────────────────────────────────────────


async def update_caller_snapshot(
    params: FunctionCallParams,
    call_state: CallState,
    caller_name: str | None = None,
    relationship: str | None = None,
    reason: str | None = None,
    urgency: str | None = None,
    callback_preference: str | None = None,
    best_time: str | None = None,
    last_caller_utterance: str | None = None,
    agent_handling_notes: str | None = None,
) -> None:
    """Record what you've learned about the caller. Call this as soon as you
    learn any of these — name, who they are to the owner, why they're calling,
    how urgent it is, how to reach them back. Pass only the fields you just
    learned. Pass last_caller_utterance to re-infer tone from their wording.

    Args:
        caller_name: The caller's name, if given.
        relationship: Who they are to the owner (e.g. "client", "sister",
            "vendor", "unknown").
        reason: Why they're calling, in a short phrase.
        urgency: One of "low", "normal", "high", "emergency".
        callback_preference: How they want to be reached back (e.g. "call back",
            "text", "email").
        best_time: When is best to reach them (e.g. "before 4pm today").
        last_caller_utterance: The caller's most recent utterance, verbatim.
            When provided, tone is inferred from the wording and the
            recommended adaptation style is returned.
        agent_handling_notes: Anything the owner should know about handling
            this caller.
    """
    snap: dict[str, Any] = call_state["caller_snapshot"]  # type: ignore[assignment]
    cs.apply_snapshot_update(
        snap,
        caller_name=caller_name,
        relationship=relationship,
        reason=reason,
        urgency=urgency,
        callback_preference=callback_preference,
        best_time=best_time,
        agent_handling_notes=agent_handling_notes,
    )
    if last_caller_utterance:
        cs.observe_caller_tone(snap, last_caller_utterance)
    cs.normalize_to_contract(snap)
    await params.result_callback(
        {
            "ok": True,
            "snapshot": cs.public_snapshot(snap),
            "style_directive": cs.live_style_directive({"caller_snapshot": snap}),
        }
    )


async def lookup_persona(
    params: FunctionCallParams,
    call_state: CallState,
    caller_number: str | None = None,
    caller_name: str | None = None,
) -> None:
    """Look up whether this caller is already known to the owner.

    Use this once at the start of a call. Real phone calls use Twilio's inbound
    caller number. Browser/WebRTC demos have no caller ID, so after asking the
    caller's name you may pass caller_name for an exact contact-book allowlist
    check. Never claim that the browser name path is strong authentication.

    Args:
        caller_number: E.164 phone number, e.g. "+14155551234". Optional.
        caller_name: Exact name stated by the caller. Browser/WebRTC fallback
            only; ignored when an inbound phone number exists.
    """
    snap: dict[str, Any] = call_state["caller_snapshot"]  # type: ignore[assignment]
    incoming_number = call_state["voicemail"]["caller_number"] or ""
    number = incoming_number or caller_number or ""
    verification_method = "incoming_phone" if incoming_number else None
    match = _persona_lookup(number)
    if match and not verification_method:
        verification_method = "stated_phone_demo"
    if not match and not incoming_number and caller_name:
        match = _persona_lookup_name(caller_name)
        if match:
            verification_method = "stated_name_demo"
    if match:
        snap["known_caller"] = True
        snap["persona_id"] = match["persona_id"]
        snap["contact_verification"] = verification_method
        if match.get("relationship") and not snap.get("relationship"):
            snap["relationship"] = match["relationship"]
        # Pre-fill the caller's name from the contact book so the bot can greet
        # them by name instead of asking — but never overwrite a name the caller
        # has already stated for themselves.
        if match.get("name") and not snap.get("caller_name"):
            snap["caller_name"] = match["name"]
    else:
        snap["known_caller"] = False
        snap["persona_id"] = None
        snap["contact_verification"] = None
    cs.normalize_to_contract(snap)

    await params.result_callback(
        {
            "known_caller": snap["known_caller"],
            "persona_id": snap["persona_id"],
            "caller_name": snap.get("caller_name"),
            "relationship": snap.get("relationship"),
            "private_context_allowed": bool(match),
            "verification_method": verification_method,
            "greeting_hint": (
                f"This is {snap['caller_name']} from your contacts — greet them by name."
                if match and snap.get("caller_name")
                else "Caller not recognised — ask for their name."
            ),
        }
    )


def greeting_for_caller(call_state: Any) -> str | None:
    """Recognise the inbound caller from the owner's contact book at call start.

    P1 calls this **once** in ``on_client_connected`` (Twilio path, where the
    number is known at connect) so the bot can greet a known caller by name on
    the very first turn instead of asking. It folds the recognised
    name/relationship into the live snapshot so the rest of the call stays
    consistent, then returns the caller's name (or ``None`` if unrecognised).

    Never raises — a lookup hiccup must not break call setup.

    Example (P1, in ``on_client_connected``)::

        name = persona_tools.greeting_for_caller(call_state)
        opener = (
            f"A caller just connected. Your contacts show this is {name}. "
            "Greet them by name and ask how you can help."
            if name else
            "A caller just connected. Greet them warmly and ask how you can help."
        )
        context.add_message({"role": "user", "content": opener})
    """
    try:
        snap: dict[str, Any] = call_state["caller_snapshot"]  # type: ignore[assignment]
        number = call_state["voicemail"]["caller_number"] or ""
        match = _persona_lookup(number)
        if not match:
            snap["known_caller"] = False
            snap["persona_id"] = None
            cs.normalize_to_contract(snap)
            return None
        snap["known_caller"] = True
        snap["persona_id"] = match["persona_id"]
        if match.get("relationship") and not snap.get("relationship"):
            snap["relationship"] = match["relationship"]
        if match.get("name") and not snap.get("caller_name"):
            snap["caller_name"] = match["name"]
        cs.normalize_to_contract(snap)
        return snap.get("caller_name")
    except Exception as exc:  # never break call setup
        logger.warning(f"greeting_for_caller skipped — {exc}")
        return None


def _persona_lookup(number: str) -> dict[str, Any] | None:
    """Resolve a caller number to a known persona.

    Checks, in order:
      1. the owner themselves (``OWNER_PHONE_NUMBER``), then
      2. the owner's contact book (macOS Contacts DB / vCard, via ``contacts``).

    Returns ``{"persona_id", "name", "relationship"}`` or ``None`` if unknown.
    """
    if not number:
        return None
    if number == os.getenv("OWNER_PHONE_NUMBER"):
        return {"persona_id": "owner", "name": None, "relationship": "the owner themselves"}
    hit = contacts.lookup(number)
    if hit:
        return {
            "persona_id": hit["name"],  # name doubles as a stable, human persona id
            "name": hit["name"],
            "relationship": hit.get("relationship"),
        }
    return None


def _persona_lookup_name(name: str) -> dict[str, Any] | None:
    """Resolve an exact contact name for the WebRTC demo fallback only."""
    hit = contacts.lookup_name(name)
    if not hit:
        return None
    return {
        "persona_id": hit["name"],
        "name": hit["name"],
        "relationship": hit.get("relationship"),
    }


_BIRTHDAY_TOPIC = re.compile(
    r"\b(birthday|bday|celebration|party|missed\s+my|couldn.?t\s+make)\b",
    re.I,
)
_HACKATHON_REASON = re.compile(
    r"(voice agent[^.\n]{0,40}hackathon|yc hackathon|hackathon project)",
    re.I,
)


def _fallback_calendar_reason(
    date_iso: str,
    *,
    caller_name: str | None = None,
    topic: str | None = None,
) -> dict[str, Any] | None:
    """Derive one caller-facing reason from curated persona when live Calendar fails."""
    from persona_context import load_persona_context

    tz_name = (os.getenv("OWNER_TZ") or "America/Los_Angeles").strip()
    today = dt.datetime.now(ZoneInfo(tz_name)).date().isoformat()
    if date_iso != today:
        return None

    query = f"{caller_name or ''} {topic or ''}".strip()
    if not query or not _BIRTHDAY_TOPIC.search(query):
        return None

    text = load_persona_context()
    if not text:
        return None

    match = _HACKATHON_REASON.search(text)
    if match:
        reason = match.group(1).strip()
    elif re.search(r"hackathon", text, re.I):
        reason = "a hackathon project"
    else:
        return None

    if len(reason) > 80:
        reason = "a hackathon project"

    return {
        "date": date_iso,
        "related_event_found": True,
        "reason": reason,
        "source": "persona_fallback",
    }


def _calendar_answer_hint(result: dict[str, Any]) -> str:
    reason = result.get("reason")
    if reason:
        return (
            f"Answer in first person: I couldn't make it because I was at {reason}. "
            "Keep it brief and do not reveal any other calendar details."
        )
    return (
        "Answer in first person that you are sorry, but do not invent a reason. "
        "Offer to follow up."
    )


async def get_calendar_context_for_caller(
    params: FunctionCallParams,
    call_state: CallState,
    date: str | None = None,
    topic: str | None = None,
) -> None:
    """Answer a known contact's calendar-context question without exposing the calendar.

    Call this only when a caller asks why the owner missed something or asks a
    similar calendar-context question. The tool checks the per-call contact gate
    itself. It returns at most one caller-facing reason, never the event list,
    locations, attendees, descriptions, or exact schedule.

    Args:
        date: Local calendar date as YYYY-MM-DD. Defaults to today.
        topic: What the caller asked about, e.g. "David Wu birthday".
    """
    snap: dict[str, Any] = call_state["caller_snapshot"]  # type: ignore[assignment]
    if not snap.get("known_caller"):
        await params.result_callback(
            {
                "authorized": False,
                "answer_hint": (
                    "Do not disclose calendar context. Ask for the caller's name and run "
                    "lookup_persona, or offer to take a message."
                ),
            }
        )
        return

    tz_name = (os.getenv("OWNER_TZ") or "America/Los_Angeles").strip()
    query_date = date
    if not query_date:
        query_date = dt.datetime.now(ZoneInfo(tz_name)).date().isoformat()
    result = google_calendar.explain_date_conflict(
        query_date,
        caller_name=str(snap.get("caller_name") or ""),
        topic=topic,
        timezone=tz_name,
    )
    if not result:
        result = _fallback_calendar_reason(
            query_date,
            caller_name=str(snap.get("caller_name") or ""),
            topic=topic,
        )
    if not result:
        api_error = google_calendar._last_api_error
        hint = "Say you are not sure and offer to take a message."
        if api_error and "disabled" in api_error.lower():
            hint = (
                "Live calendar lookup is unavailable right now. Say you are not sure "
                "what came up and offer to take a message."
            )
        await params.result_callback(
            {
                "authorized": True,
                "context_available": False,
                "answer_hint": hint,
                "api_error": api_error,
            }
        )
        return

    await params.result_callback(
        {
            "authorized": True,
            "context_available": True,
            "related_event_found": result.get("related_event_found", False),
            "answer_hint": _calendar_answer_hint(result),
            "source": result.get("source", "google_calendar"),
        }
    )


# ─── Action tools (Part B) ───────────────────────────────────────────────────
#
# We delegate to actions.make_action_tools() to keep all email/calendar logic in
# one place, then expose thin (params, call_state, …) wrappers here so they fit
# P1's bind_call_state convention.


async def send_owner_email(
    params: FunctionCallParams,
    call_state: CallState,
    subject: str | None = None,
) -> None:
    """Email the owner this voicemail's summary. Call once you've captured the
    caller's name, reason, and how to reach them back. The email always goes
    to the owner's own inbox (no third-party sending in the demo).

    Args:
        subject: Optional subject line. Defaults to a sensible one built from
            the caller's name and reason.
    """
    tools = actions.make_action_tools(
        call_state,  # type: ignore[arg-type]
        free_busy_check=calendar_free_busy_check,
    )
    send_fn = next(fn for fn in tools if fn.__name__ == "send_owner_email")
    await send_fn(params, subject=subject)
    if call_state.get("actions"):
        last = call_state["actions"][-1]  # type: ignore[index]
        if last.get("type") == "email" and last.get("status") == "sent":
            call_state["voicemail"]["email_sent"] = True


async def book_callback_slot(
    params: FunctionCallParams,
    call_state: CallState,
    start_iso: str | None = None,
    end_iso: str | None = None,
    requested_time: str | None = None,
    title: str | None = None,
) -> None:
    """Put a TENTATIVE callback event on the owner's calendar when the caller
    has asked for a specific callback time. Only call this once the caller
    requests a time/window. Coordinates with P2's calendar read side (if
    plugged in) to skip booking over a known conflict.

    Args:
        start_iso: Callback start as ISO-8601 if you can resolve it from the
            caller's words. Optional.
        end_iso: Callback end as ISO-8601. Optional; defaults to 30 min after
            start.
        requested_time: The caller's own words for when to call back. Always
            include this.
        title: Optional event title. Defaults to ``"Callback: <name>"``.
    """
    tools = actions.make_action_tools(
        call_state,  # type: ignore[arg-type]
        free_busy_check=calendar_free_busy_check,
        create_event=google_calendar.create_event,
    )
    book_fn = next(fn for fn in tools if fn.__name__ == "book_callback_slot")
    await book_fn(
        params,
        start_iso=start_iso,
        end_iso=end_iso,
        requested_time=requested_time,
        title=title,
    )
    if call_state.get("actions"):
        last = call_state["actions"][-1]  # type: ignore[index]
        if last.get("type") == "calendar" and last.get("start_iso"):
            call_state["voicemail"]["callback_slot"] = last["start_iso"]


# ─── Post-call hook (Part C — persistence) ──────────────────────────────────


def on_call_finished(call_state: CallState) -> str | None:
    """Persist the finished voicemail to DynamoDB / S3 / local fallback.

    P1 calls this from ``on_client_disconnected``. Returns the record id (or
    ``None`` on failure — persistence already logs the reason and we never
    raise into the disconnect handler, which would mask the real call end).
    """
    try:
        snap: dict[str, Any] = call_state["caller_snapshot"]  # type: ignore[assignment]
        cs.normalize_to_contract(snap)
        persona_ctx = call_state.get("persona_context") or ""
        # Use a short tag from P2's persona_context as the owner-context tag —
        # the full context lives in the prompt, the tag is just a record label.
        owner_tag = (persona_ctx[:120] + "…") if len(persona_ctx) > 120 else persona_ctx
        record = persistence.persist_voicemail(
            voicemail=dict(call_state["voicemail"]),  # type: ignore[arg-type]
            caller_snapshot=cs.public_snapshot(snap),
            actions_taken=list(call_state.get("actions") or []),  # type: ignore[arg-type]
            owner_context_tag=owner_tag or None,
        )
        record_id = record.get("record_id")
        logger.info(f"P3 on_call_finished: persisted {record_id} via {persistence.backend_kind()}")

        # Index this call by caller and expand their cumulative profile. Keyed by
        # phone number; grows a little with every call. Isolated so a profile
        # failure never loses the voicemail record we just wrote.
        try:
            vm: dict[str, Any] = dict(call_state["voicemail"])  # type: ignore[arg-type]
            msg: dict[str, Any] = dict(vm.get("message") or {})
            persistence.upsert_caller_profile(
                vm.get("caller_number"),
                caller_name=msg.get("caller_name"),
                reason=msg.get("reason"),
                urgency=msg.get("urgency"),
                callback_number=msg.get("callback_number"),
                summary=vm.get("summary"),
                persona_id=snap.get("persona_id"),
                call_record_id=record_id,
            )
        except Exception as exc:
            logger.error(f"P3 on_call_finished: caller profile skipped — {exc}")

        return record_id
    except Exception as exc:
        logger.error(f"P3 on_call_finished: persistence skipped — {exc}")
        return None


# ─── Transcript → tone listener (auto tone inference) ────────────────────────
#
# So tone inference doesn't depend on the LLM remembering to pass
# ``last_caller_utterance`` into update_caller_snapshot, P1 inserts the
# processor returned by ``make_transcript_tone_processor`` into the pipeline
# between ``stt`` and the user aggregator. It observes only FINAL caller
# transcripts (never interim — those would flicker the tone) and folds the
# inferred tone into the live snapshot. Wording-only: it reads frame text, not
# audio, so the privacy invariant holds.


def observe_transcript_frame(call_state: Any, frame: Any) -> str | None:
    """Fold one final caller transcript into the live snapshot's tone.

    Pure helper — no Pipecat import — so it is unit-testable with any object
    exposing a ``.text`` attribute. Returns the inferred tone label, or ``None``
    for empty/blank text. Never raises.
    """
    text = getattr(frame, "text", None)
    if not text or not str(text).strip():
        return None
    snap: dict[str, Any] = call_state["caller_snapshot"]  # type: ignore[assignment]
    tone = cs.observe_caller_tone(snap, str(text))
    cs.normalize_to_contract(snap)
    return tone


def make_transcript_tone_processor(call_state: CallState, context: Any | None = None) -> Any:
    """Build a passthrough Pipecat ``FrameProcessor`` that auto-infers caller
    tone from final transcripts.

    Insert it in the pipeline between ``stt`` and the user aggregator::

        tone_listener = persona_tools.make_transcript_tone_processor(call_state, context)
        Pipeline([transport.input(), stt, tone_listener, user_aggregator, ...])

    Pipecat is imported lazily so this module stays importable in test
    environments that have no live pipeline.
    """
    from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class _TranscriptToneListener(FrameProcessor):
        def __init__(self, state: CallState, llm_context: Any | None) -> None:
            super().__init__()
            self._state = state
            self._context = llm_context

        async def process_frame(self, frame: Any, direction: FrameDirection) -> None:
            await super().process_frame(frame, direction)
            # Final caller transcripts only — interim frames would flicker tone.
            if isinstance(frame, TranscriptionFrame) and not isinstance(
                frame, InterimTranscriptionFrame
            ):
                try:
                    observe_transcript_frame(self._state, frame)
                    if self._context is not None:
                        refresh_live_style_directive(self._state, self._context)
                except Exception as exc:  # never break the audio pipeline
                    logger.warning(f"transcript tone listener skipped — {exc}")
            await self.push_frame(frame, direction)

    return _TranscriptToneListener(call_state, context)


# ─── Registry append (runs at import time) ───────────────────────────────────

TOOL_REGISTRY.extend(
    [
        update_caller_snapshot,
        lookup_persona,
        get_calendar_context_for_caller,
        send_owner_email,
        book_callback_slot,
    ]
)

logger.info(
    "persona_tools loaded — registered 5 P3 tools; "
    f"persistence backend={persistence.backend_kind()}"
)
