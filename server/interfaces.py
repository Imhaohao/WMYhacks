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
from typing import TypedDict


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
            recorded_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            message={},
            action_items=[],
            callback_slot=None,
            sms_sent=False,
            email_sent=False,
        ),
        caller_snapshot=CallerSnapshot(),
        persona_context="",  # P2 must set this before build_system_instruction() is called
    )


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
    if caller_name:
        caller_line = f"Recognised caller: {caller_name} ({caller_number})."
    elif caller_number:
        caller_line = f"Unknown caller. Incoming number: {caller_number}."
    else:
        caller_line = "Unknown caller. No phone number available (WebRTC / direct connection)."

    return (
        "You are a personal voicemail assistant acting as a proxy for the owner. "
        "Answer calls on their behalf, take complete messages, and route urgent "
        "matters appropriately.\n\n"
        # ── OWNER CONTEXT (P2 injects persona_context here) ──────────────────
        "## Owner Context\n"
        f"{persona_block}\n\n"
        # ─────────────────────────────────────────────────────────────────────
        "## This Call\n"
        f"{caller_line}\n"
        f"Today is {today.strftime('%A, %B %d, %Y')}.\n\n"
        # ── WORKFLOW — P1 defines the step sequence ───────────────────────────
        "## Workflow — follow this order exactly\n"
        "Ask ONE question per turn. Call the tool as soon as you have a confirmed "
        "answer. Never ask two things in the same sentence.\n\n"
        "Step 1 — Greet and get their name.\n"
        "   Say: 'Hi, you've reached [owner]'s voicemail assistant. May I ask who's calling?'\n"
        "   → call capture_caller_identity once they give their name.\n\n"
        "Step 2 — Reason for calling.\n"
        "   Ask: 'And what would you like to leave a message about?'\n"
        "   → call capture_message_reason once they explain.\n\n"
        "Step 3 — Urgency.\n"
        "   Ask: 'How urgent is this — urgent, normal, or can it wait?'\n"
        "   → call capture_urgency with 'urgent', 'normal', or 'low'.\n"
        "   If their topic matches a priority in Owner Context, say so: "
        "'I'll make sure this gets flagged as urgent.'\n\n"
        "Step 4 — Callback preference.\n"
        "   Ask: 'Would you like a callback? If so, what number and what time works?'\n"
        "   → call capture_callback_preference.\n"
        "   If they don't want a callback, pass wants_callback=False and omit the other args.\n\n"
        "Step 5 — Confirm the message.\n"
        "   Call get_voicemail_summary. Read the result back to the caller word for word.\n"
        "   Ask: 'Does that sound right?'\n"
        "   If they correct anything, call the relevant capture_* tool again, "
        "then call get_voicemail_summary again before continuing.\n\n"
        "Step 6 — Finish and hang up.\n"
        "   Call finish_voicemail to save the message.\n"
        "   In the same turn say a short goodbye "
        "(e.g. 'Great, I'll pass that along. Talk soon!')\n"
        "   then call end_call.\n\n"
        # ─────────────────────────────────────────────────────────────────────
        "## Phone Etiquette\n"
        "- 1–2 short sentences per turn. Longer only at step 5 (reading back the summary).\n"
        "- No filler openers ('Absolutely!', 'Perfect!', 'Great!'). Go straight to the point.\n"
        "- No bullet points, no emojis. Responses are spoken aloud.\n"
        "- Use contractions. Fragments are fine.\n"
        "- Do NOT reveal the owner's location, exact schedule, or personal details.\n\n"
        # ── P3 NOTE (caller_snapshot) ─────────────────────────────────────────
        "## Tone Adaptation\n"
        "Adapt your tone to the caller's affect — efficient with rushed callers, "
        "warm with distressed ones, calm with hostile ones.\n"
        # ─────────────────────────────────────────────────────────────────────
    )
