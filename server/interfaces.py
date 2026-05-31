"""
Shared interfaces for the voicemail agent — the contract all three teammates
build against. Import from here; do not duplicate these definitions.

Ownership map:
    call_state["voicemail"]         — P1 (voice path, Twilio, TTS, action layer)
    call_state["caller_snapshot"]   — P3 (live tone/persona detection, DynamoDB)
    call_state["persona_context"]   — P2 (owner context: Calendar, iMessage priorities)

    TOOL_REGISTRY                   — P1 seeds; P2 and P3 append their tools
    build_system_instruction()      — P1 owns the skeleton; <<PERSONA_CONTEXT>>
                                      is replaced by P2's injected string at call time
"""

from __future__ import annotations

import datetime
from typing import Any, TypedDict, cast

import caller_snapshot

# ─── CallState ────────────────────────────────────────────────────────────────


class VoicemailState(TypedDict):
    """Owned by P1. Populated throughout the call and persisted at end."""

    transcript: str          # running verbatim transcript (P1 appends each turn)
    summary: str             # human-readable summary — written by finish_voicemail
    caller_number: str       # E.164 (Twilio) or "" (WebRTC / unknown)
    duration_seconds: int    # updated live by P1 every 30 s
    recorded_at: str         # ISO-8601 UTC timestamp when call started
    # Structured message fields — written incrementally by capture_* tools (P1)
    message: dict            # keys: caller_name, reason, urgency,
                             #       wants_callback, callback_number,
                             #       callback_preferred_time
    action_items: list[str]  # one-line action strings, written by finish_voicemail
    callback_slot: str | None  # ISO datetime if P2 books a calendar slot
    sms_sent: bool           # True once Twilio SMS dispatched to owner
    email_sent: bool         # True once Gmail email dispatched to owner


class CallerSnapshot(TypedDict, total=False):
    """
    Owned by P3. Updated live during the call by P3's snapshot tools.
    The LLM adapts its tone from these signals; snapshot is stored in
    DynamoDB at call end alongside the voicemail record.

    All fields are optional (total=False) — P3 fills them incrementally.
    """

    tone: str              # "urgent" | "casual" | "hostile" | "distressed" | …
    sentiment_score: float  # -1.0 (negative) → +1.0 (positive)
    intent: str            # "leave_message" | "urgent_callback" | "hang_up" | …
    known_caller: bool     # True if number matched a stored persona
    persona_id: str | None  # matched persona key in the contacts store, if any


class CallState(TypedDict):
    """
    Top-level per-call state.  One instance is created by default_call_state()
    at the start of each call and passed (by reference) into every tool and
    into build_system_instruction().

    Do not add ad-hoc keys here.  Extend the appropriate sub-TypedDict instead
    so ownership stays clear and teammates don't step on each other.
    """

    voicemail: VoicemailState         # P1
    caller_snapshot: CallerSnapshot   # P3
    persona_context: str              # P2 — see build_system_instruction()
    actions: list[dict[str, Any]]     # P3 — email/calendar action history


def default_call_state(caller_number: str = "") -> CallState:
    """Factory: returns a zeroed CallState for a new call.

    Call this once per incoming call (in the ``run_bot`` function) and close
    over the returned dict in all tool functions.
    """
    return CallState(
        voicemail=VoicemailState(
            transcript="",
            summary="",
            caller_number=caller_number,
            duration_seconds=0,
            recorded_at=datetime.datetime.now(datetime.UTC).isoformat(),
            message={},
            action_items=[],
            callback_slot=None,
            sms_sent=False,
            email_sent=False,
        ),
        caller_snapshot=cast(CallerSnapshot, caller_snapshot.fresh_snapshot()),
        persona_context="",  # P2 must set this before build_system_instruction() is called
        actions=[],
    )


def build_caller_confirmation(message: dict[str, Any]) -> str:
    """Build the short spoken recap for a caller without exposing internal tags."""
    name = message.get("caller_name") or "you"
    reason = message.get("reason") or "your message"
    confirmation = f"I have {name} calling about {reason}"
    if message.get("wants_callback"):
        callback_number = message.get("callback_number") or ""
        confirmation += (
            f", and you'd like a callback at {callback_number}"
            if callback_number
            else ", and you'd like a callback"
        )
        callback_time = message.get("callback_preferred_time") or ""
        if callback_time:
            confirmation += f" {callback_time}"
    return confirmation + ". Is that right?"


# ─── Tool Registry ────────────────────────────────────────────────────────────
#
# The single list bot-nemotron.py reads to build its ToolsSchema and register
# handlers with the LLM.
#
# HOW TO ADD TOOLS (for P2 and P3):
#   1. Define your async tool function in your module:
#
#       async def my_tool(params: FunctionCallParams, arg: str) -> None:
#           """Docstring becomes the LLM tool description."""
#           ...
#           await params.result_callback({"ok": True})
#
#   2. At module level (after the function), append to this list:
#
#       from interfaces import TOOL_REGISTRY
#       TOOL_REGISTRY.append(my_tool)
#
#   3. Import your module somewhere before bot-nemotron.py starts the pipeline
#      (a bare ``import my_module`` at the top of bot-nemotron.py is enough).
#
# RULES:
#   - Each entry must be an async callable matching Pipecat's FunctionCallParams
#     signature.
#   - Tool names must be unique across all modules — coordinate to avoid clashes.
#   - P1's core tools (capture_*, finish_voicemail, end_call) are closures defined
#     inside run_bot — they close over call_state directly and are NOT in this list.
#   - P2 appends: book_callback_slot, get_calendar_availability, …
#   - P3 appends: update_caller_snapshot, lookup_persona, …
#   - If your tool needs call_state, declare it as the second parameter named
#     `call_state: CallState` — bot-nemotron.py's bind_call_state() will wrap it.

TOOL_REGISTRY: list = []  # bot-nemotron.py reads this at pipeline startup


# ─── System Instruction Builder ───────────────────────────────────────────────


def build_system_instruction(
    call_state: CallState,
    caller_name: str | None = None,
    today: datetime.date | None = None,
) -> str:
    """
    Assemble the per-call system instruction.

    ┌─────────────────────────────────────────────────────────────────────┐
    │  PERSONA INJECTION POINT (P2)                                       │
    │                                                                     │
    │  Before calling this function, P2 sets:                             │
    │      call_state["persona_context"] = <live owner context string>    │
    │                                                                     │
    │  Format P2 should produce:                                          │
    │    • Owner availability window  (e.g. "Available after 3 pm")      │
    │    • Priority contacts / topics (from iMessage analysis)            │
    │    • Standing instructions      (e.g. "Always take msgs from Mom")  │
    │                                                                     │
    │  If persona_context is empty the default fallback below is used,   │
    │  so the bot still functions without P2's module loaded.             │
    └─────────────────────────────────────────────────────────────────────┘

    Args:
        call_state:   The CallState for this call (must already have
                      caller_number and persona_context populated).
        caller_name:  Human-readable name if the caller was recognised.
        today:        Inject for testing; defaults to datetime.date.today().
    """
    if today is None:
        today = datetime.date.today()

    # ── P2 INJECTION ─────────────────────────────────────────────────────────
    # call_state["persona_context"] is the live owner context P2 injects.
    # If it's empty (P2 module not loaded / not yet run), use a safe default.
    persona_block: str = call_state["persona_context"] or (
        "No live owner context available. Treat all callers as standard priority. "
        "Do not commit to specific callback times — tell the caller the owner will "
        "be in touch as soon as possible."
    )
    # ─────────────────────────────────────────────────────────────────────────

    caller_number = call_state["voicemail"]["caller_number"]
    web_demo = not (caller_number or "").strip()
    if caller_name:
        caller_line = f"Recognised caller: {caller_name} ({caller_number})."
    elif caller_number:
        caller_line = f"Unknown caller. Incoming number: {caller_number}."
    else:
        caller_line = "Unknown caller. No phone number available (WebRTC / direct connection)."

    if web_demo:
        callback_gathering = (
            "If they want the owner to call them back, ask whether they want a callback "
            "and when works best — do NOT ask for a phone number (this is a browser demo; "
            "there is no caller ID). Use capture_callback_preference with wants_callback and "
            "optional callback_preferred_time only; always leave callback_number empty.\n\n"
        )
        sms_urgent_rule = (
            "     • Urgency is 'urgent' (no callback number on this channel).\n"
        )
    else:
        callback_gathering = (
            "If they want a reply, capture whether they want a callback, the number to "
            "reach them, and when works best via capture_callback_preference.\n\n"
        )
        sms_urgent_rule = (
            "     • Urgency is 'urgent' AND a callback number was given.\n"
        )

    return (
        "You are a conversational personal assistant acting as a proxy for the owner. "
        "Answer calls naturally on their behalf. You can take a message or answer a "
        "quick question when the private owner context supports a safe answer. For safe "
        "owner-proxy answers, speak in first person: say I, me, and my. Never refer to "
        "the owner by name or as a third party when answering the caller.\n\n"
        # ── OWNER CONTEXT (P2 injects persona_context here) ──────────────────
        "## Owner Context\n"
        f"{persona_block}\n\n"
        # ─────────────────────────────────────────────────────────────────────
        "## This Call\n"
        f"{caller_line}\n"
        f"Today is {today.strftime('%A, %B %d, %Y')}.\n\n"
        # ── CONVERSATION POLICY — P1 defines the branches ─────────────────────
        "## Conversation Policy\n"
        "Keep the call natural. Ask at most ONE question per turn. Do not force every "
        "caller through a form or announce internal workflow steps.\n\n"
        "Start by greeting the caller and offering two paths: take a message, or help "
        "with a quick question. If an incoming phone number is available, call "
        "lookup_persona once near the start. If the caller is recognised, confirm "
        "their name naturally. Otherwise ask their name when it becomes useful. In a "
        "browser/WebRTC demo, there is no caller ID: before using private context, ask "
        "their name and call lookup_persona with caller_name for an exact contact-book "
        "allowlist check.\n\n"
        "## Quick Questions\n"
        "For a caller's question, answer conversationally only when Owner Context "
        "supports a safe, low-risk answer. Speak in the owner's concise style, but never "
        "invent a fact or action. If the answer is missing, uncertain, sensitive, or would "
        "require a commitment, say you are not sure and offer to take a message.\n"
        "Before answering ANY question that uses private calendar context, first call "
        "lookup_persona. Only if that returns private_context_allowed=true may you call "
        "get_calendar_context_for_caller. Follow its answer_hint. Never disclose the "
        "calendar event list, exact schedule, location, attendees, or unrelated events.\n"
        "Treat Owner Context as private reasoning material. Never quote it, list it, "
        "mention its sources, reveal hidden priorities, expose message history, or disclose "
        "the owner's exact location, schedule, contact details, or personal data. Do not "
        "say you read the owner's emails, AI prompts, or messages.\n\n"
        "## Message Taking\n"
        "When the caller wants to leave a message, gather only what is useful: their name, "
        "what the message is about, and callback details if they want a reply. Call each "
        "capture_* tool as soon as its answer is clear. Infer urgency from the caller's "
        "words and Owner Context; call capture_urgency without asking them to choose an "
        "internal label unless you genuinely need clarification. Do not say urgency labels "
        "or snapshot fields aloud.\n\n"
        f"{callback_gathering}"
        "After each meaningful caller turn, call update_caller_snapshot with newly learned "
        "details and follow any CALLER ADAPTATION system note. The snapshot is internal "
        "reasoning for the owner; never read it back or mention it.\n\n"
        "If the caller asks for a callback at a specific time or window, call "
        "book_callback_slot. Pass requested_time exactly as they said it, plus "
        "start_iso/end_iso if you can resolve them.\n\n"
        "Call notify_owner_sms if ANY of these apply:\n"
        "     • The caller explicitly asks to notify the owner right away.\n"
        f"{sms_urgent_rule}"
        "     • Owner context flags this caller or topic as priority.\n"
        "   Do NOT call it on every message — only when same-turn notification matters.\n"
        "   Pass a brief note= if there is context the one-line summary can't carry\n"
        "   (e.g. note='caller is waiting outside the building').\n\n"
        "When the message is complete, call get_voicemail_summary and say only its short "
        "caller-facing confirmation. Never add internal urgency, tone, classification, or "
        "caller snapshot details.\n"
        "   If they correct anything, call the relevant capture_* tool again, "
        "then call get_voicemail_summary again before continuing.\n\n"
        "Once the caller confirms the summary, call send_owner_email. Do this for every completed "
        "message, not only urgent ones.\n\n"
        "Call finish_voicemail to save a completed message.\n"
        "   In the same turn say a short goodbye "
        "(e.g. 'Great, I'll pass that along. Talk soon!')\n"
        "   then call end_call. For a quick-question call with no message, say a short "
        "goodbye and call end_call without inventing voicemail details.\n"
        "   If the caller says they are done, finished, or goodbye, respond briefly "
        "and call end_call immediately — do not ask another question.\n\n"
        # ─────────────────────────────────────────────────────────────────────
        "## Phone Etiquette\n"
        "- 1–2 short sentences per turn. Keep message confirmations short.\n"
        "- No filler openers ('Absolutely!', 'Perfect!', 'Great!'). Go straight to the point.\n"
        "- No bullet points, no emojis. Responses are spoken aloud.\n"
        "- Use contractions. Fragments are fine.\n"
        "- Do NOT reveal the owner's location, exact schedule, or personal details.\n\n"
        # ── P3 NOTE (caller_snapshot) ─────────────────────────────────────────
        "## Tone Adaptation\n"
        "Adapt your tone to the caller's affect — efficient with rushed callers, "
        "warm with distressed ones, calm with hostile ones. Never mirror hostility, "
        "insult the caller, challenge them, or say phrases like 'spit it out.' Mild "
        "teasing is not a reason to threaten to end the call.\n"
        # ─────────────────────────────────────────────────────────────────────
    )
