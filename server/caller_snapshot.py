#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Ephemeral caller snapshot for the voicemail proxy (Person 3, Part A).

A *caller snapshot* is a small, current-call-only read on **who is calling and
how to handle them**. It is built up live during a single call and thrown away
when the call ends — there is no persistent caller profile, no voiceprint, no
speaker ID, and no raw audio. The only thing that survives the call is the
structured voicemail record written by ``persistence.py`` (which embeds a copy
of the snapshot the owner explicitly gets to see).

This module is deliberately decoupled from Pipecat so the inference and update
logic can be unit-tested without spinning up a pipeline. The Pipecat tool
functions are thin closures produced by :func:`make_snapshot_tools`.

Integration seam (plugs into P1's frozen ``call_state``):

    from caller_snapshot import fresh_snapshot, make_snapshot_tools, live_style_directive

    call_state["caller_snapshot"] = fresh_snapshot()          # fresh per call
    tool_functions += make_snapshot_tools(call_state)          # append to shared list
    # ...and refresh `live_style_directive(call_state)` into the system prompt each turn.

See ``INTEGRATION_P3.md`` for the exact wiring.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# --- Snapshot shape ---------------------------------------------------------

# The nine owner-facing fields (Part A, task 1). Order matters for the summary.
SNAPSHOT_FIELDS: tuple[str, ...] = (
    "caller_name",
    "relationship",
    "reason",
    "urgency",
    "emotional_tone",
    "communication_style",
    "callback_preference",
    "best_time",
    "agent_handling_notes",
)

# Allowed emotional-tone labels, inferred from transcript wording ONLY
# (task 2). We never use audio, pitch, or prosody — just the words.
TONE_LABELS: tuple[str, ...] = (
    "rushed",
    "calm",
    "confused",
    "upset",
    "concise",
    "rambling",
    "hesitant",
)

# Urgency is a small controlled vocabulary so downstream triage is stable.
URGENCY_LEVELS: tuple[str, ...] = ("low", "normal", "high", "emergency")


def fresh_snapshot() -> dict[str, Any]:
    """Return a brand-new, empty snapshot. Call once per call.

    Every field starts ``None`` (unknown). ``tone_history`` and ``updated_at``
    are internal/ephemeral bookkeeping, not owner-facing fields.
    """
    snap: dict[str, Any] = {f: None for f in SNAPSHOT_FIELDS}
    snap["tone_history"] = []  # list[str], most-recent-last; ephemeral
    snap["updated_at"] = None
    return snap


# --- Tone inference (transcript wording only) -------------------------------

# Keyword / pattern cues per tone. Inference is intentionally simple and
# explainable — it reads the caller's *words*, never their voice. Cues are
# lowercased substring or regex checks.
_TONE_CUES: dict[str, list[str]] = {
    "rushed": [
        r"\b(hurry|quick(ly)?|asap|right now|no time|gotta go|in a rush|"
        r"real quick|make it fast|don'?t have (much |a lot of )?time)\b",
        r"\b(before|by) \d",  # "before 4", "by 5pm"
    ],
    "upset": [
        r"\b(angry|upset|furious|frustrat(ed|ing)|ridiculous|unacceptable|"
        r"terrible|awful|fed up|sick of|complain(t|ing)?|not happy|so mad)\b",
        r"!{2,}",
    ],
    "confused": [
        r"\b(confus(ed|ing)|i don'?t (get|understand)|not sure what|"
        r"what do you mean|i'?m lost|huh\??|wait,? what|is this the right)\b",
        r"\?{2,}",
    ],
    "hesitant": [
        r"\b(um+|uh+|er+|hmm+|i guess|maybe|i think,? maybe|not really sure|"
        r"sort of|kind of|i don'?t know,? (uh|um)|well,? i)\b",
        r"\.\.\.",
    ],
    "rambling": [],  # detected structurally (length / run-ons), see below
    "concise": [],   # detected structurally (very short turns)
    "calm": [],      # fallback when nothing else fires
}


def infer_tone_from_text(text: str) -> str | None:
    """Infer a single emotional-tone label from one caller utterance.

    Uses **wording only** — keyword cues plus simple structural signals
    (utterance length, run-ons, filler). Returns one of :data:`TONE_LABELS`,
    or ``None`` if the text is empty. This is deterministic and explainable on
    purpose; it is not a sentiment model and reads no audio.

    Precedence (strongest signal wins): upset > confused > rushed > hesitant >
    rambling > concise > calm.
    """
    if not text or not text.strip():
        return None

    lowered = text.lower().strip()
    words = re.findall(r"\w+", lowered)
    n_words = len(words)

    def hit(tone: str) -> bool:
        return any(re.search(p, lowered) for p in _TONE_CUES[tone])

    # Strong emotional / comprehension signals first.
    if hit("upset"):
        return "upset"
    if hit("confused"):
        return "confused"
    if hit("rushed"):
        return "rushed"
    if hit("hesitant"):
        return "hesitant"

    # Structural signals.
    # Rambling: long single turn or several run-on clauses chained with "and"/commas.
    sentence_breaks = len(re.findall(r"[.!?]", lowered))
    run_on = n_words >= 40 or (n_words >= 25 and lowered.count(" and ") >= 3)
    if run_on and sentence_breaks <= max(1, n_words // 40):
        return "rambling"
    # Concise: a short, complete turn with no filler.
    if n_words <= 6:
        return "concise"

    return "calm"


# --- Adaptation guidance (tone -> how the agent should behave) --------------

# Task 3 mapping. These are short, spoken-style directives the LLM can act on.
_ADAPTATION: dict[str, str] = {
    "rushed": "Caller is rushed — be brief, skip pleasantries, ask only the essential question.",
    "upset": "Caller is upset — slow down, stay warm and calm, acknowledge the frustration before asking anything.",
    "confused": "Caller is confused — ask one simple question at a time and avoid jargon or compound questions.",
    "rambling": "Caller is rambling — gently add structure: summarize, then ask one focused question to move forward.",
    "concise": "Caller is concise — mirror them: short, direct replies, no filler.",
    "hesitant": "Caller is hesitant — be reassuring and patient; offer gentle prompts and don't rush them.",
    "calm": "Caller is calm — keep a friendly, natural pace; one question at a time.",
}


def adaptation_guidance(tone: str | None) -> str:
    """Return the one-line behavioral directive for an emotional tone."""
    if not tone:
        return _ADAPTATION["calm"]
    return _ADAPTATION.get(tone, _ADAPTATION["calm"])


def live_style_directive(call_state: dict[str, Any]) -> str:
    """Build the system-prompt fragment the bot injects each turn so behavior
    visibly adapts to the current caller.

    P1's ``run_bot`` should refresh this into the live context (e.g. as a system
    message) after each user turn. It folds in the current tone, communication
    style, and urgency so the model's next reply matches the caller.
    """
    snap = call_state.get("caller_snapshot") or {}
    tone = snap.get("emotional_tone")
    directive = adaptation_guidance(tone)
    extra: list[str] = []
    if snap.get("urgency") in ("high", "emergency"):
        extra.append("This is time-sensitive — prioritize getting the callback details right.")
    style = snap.get("communication_style")
    if style:
        extra.append(f"Match their communication style: {style}.")
    if extra:
        directive = directive + " " + " ".join(extra)
    return "CALLER ADAPTATION: " + directive


# --- Snapshot updates -------------------------------------------------------

def apply_snapshot_update(snapshot: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """Merge non-empty field updates into ``snapshot`` in place and return it.

    Only known :data:`SNAPSHOT_FIELDS` are accepted; unknown keys are ignored.
    Empty strings / ``None`` are treated as "no change" so a partial update
    never wipes a field that was already captured. ``urgency`` and
    ``emotional_tone`` are normalized to their controlled vocabularies when
    possible (otherwise stored as given).
    """
    for key, value in fields.items():
        if key not in SNAPSHOT_FIELDS:
            continue
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue
            if key == "urgency":
                low = value.lower()
                value = low if low in URGENCY_LEVELS else value
            if key == "emotional_tone":
                low = value.lower()
                value = low if low in TONE_LABELS else value
        snapshot[key] = value
    snapshot["updated_at"] = datetime.now(UTC).isoformat()
    return snapshot


def observe_caller_tone(snapshot: dict[str, Any], utterance: str) -> str | None:
    """Infer tone from one caller utterance and fold it into the snapshot.

    Appends to ``tone_history`` and sets ``emotional_tone`` to the latest
    inferred label. Returns the inferred label (or ``None`` for empty input).
    Communication_style is derived from the recent tone history as a coarse,
    human-glanceable phrase.
    """
    tone = infer_tone_from_text(utterance)
    if tone is None:
        return None
    history: list[str] = snapshot.setdefault("tone_history", [])
    history.append(tone)
    snapshot["emotional_tone"] = tone
    snapshot["communication_style"] = _summarize_style(history)
    snapshot["updated_at"] = datetime.now(UTC).isoformat()
    return tone


def _summarize_style(history: list[str]) -> str:
    """Collapse recent tone history into a short communication-style phrase."""
    if not history:
        return "unknown"
    recent = history[-5:]
    # Most frequent recent tone, tie-broken by most recent.
    counts: dict[str, int] = {}
    for t in recent:
        counts[t] = counts.get(t, 0) + 1
    dominant = max(recent, key=lambda t: (counts[t], recent.index(t)))
    mapping = {
        "rushed": "fast and to-the-point",
        "upset": "frustrated, needs reassurance",
        "confused": "needs things kept simple",
        "concise": "short and direct",
        "rambling": "talkative, gives lots of detail",
        "hesitant": "tentative, thinks out loud",
        "calm": "relaxed and conversational",
    }
    return mapping.get(dominant, dominant)


# --- Owner-facing summary ---------------------------------------------------

_FIELD_LABELS: dict[str, str] = {
    "caller_name": "Caller",
    "relationship": "Relationship",
    "reason": "Reason for call",
    "urgency": "Urgency",
    "emotional_tone": "Tone (from wording)",
    "communication_style": "Communication style",
    "callback_preference": "Callback preference",
    "best_time": "Best time",
    "agent_handling_notes": "Handling notes",
}


def format_snapshot_summary(snapshot: dict[str, Any]) -> str:
    """Render the snapshot as a short, human-readable block for the final
    voicemail summary (task 4). Unknown fields are shown as ``—``."""
    lines = ["Caller snapshot:"]
    for field in SNAPSHOT_FIELDS:
        value = snapshot.get(field)
        shown = value if value not in (None, "") else "—"
        lines.append(f"  {_FIELD_LABELS[field]}: {shown}")
    return "\n".join(lines)


def public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the owner-facing snapshot fields only (drops internal bookkeeping
    like ``tone_history``). Used when embedding the snapshot in a persisted
    record or an email."""
    out = {f: snapshot.get(f) for f in SNAPSHOT_FIELDS}
    out["tone_history"] = list(snapshot.get("tone_history") or [])
    out["updated_at"] = snapshot.get("updated_at")
    return out


# --- P1 CallerSnapshot contract bridge --------------------------------------
#
# server/interfaces.py defines the team-wide CallerSnapshot TypedDict (total=False)
# with these fields:
#     tone:             "urgent" | "casual" | "hostile" | "distressed" | …
#     sentiment_score:  -1.0 … +1.0
#     intent:           "leave_message" | "urgent_callback" | "hang_up" | …
#     known_caller:     bool
#     persona_id:       str | None
#
# Our rich fields (caller_name, emotional_tone, urgency, …) are allowed extras
# because the TypedDict is total=False. We still populate the five contract
# fields so any other module reading the shared shape gets meaningful values.

_TONE_CONTRACT_MAP: dict[str, str] = {
    "rushed": "urgent",
    "upset": "hostile",
    "confused": "distressed",
    "hesitant": "distressed",
    "rambling": "casual",
    "concise": "casual",
    "calm": "casual",
}

_SENTIMENT_BY_TONE: dict[str, float] = {
    "rushed": -0.1,
    "upset": -0.7,
    "confused": -0.3,
    "hesitant": -0.2,
    "rambling": 0.1,
    "concise": 0.0,
    "calm": 0.3,
}


def normalize_to_contract(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project our rich snapshot onto P1's frozen ``CallerSnapshot`` fields.

    Mutates and returns ``snapshot``. Safe to call repeatedly — each call just
    re-derives the contract fields from the current rich values, so calling it
    after every snapshot update keeps the two views in sync.
    """
    fine = snapshot.get("emotional_tone")
    snapshot["tone"] = _TONE_CONTRACT_MAP.get(fine, "casual") if fine else "casual"
    snapshot["sentiment_score"] = _SENTIMENT_BY_TONE.get(fine, 0.0) if fine else 0.0
    urg = (snapshot.get("urgency") or "").lower()
    if urg in ("high", "emergency"):
        snapshot["intent"] = "urgent_callback"
    elif urg in ("low", "normal"):
        snapshot["intent"] = "leave_message"
    else:
        snapshot["intent"] = snapshot.get("intent") or "leave_message"
    snapshot.setdefault("known_caller", False)
    snapshot.setdefault("persona_id", None)
    return snapshot


# --- Pipecat tool factory ---------------------------------------------------

def make_snapshot_tools(call_state: dict[str, Any]) -> list[Callable[..., Any]]:
    """Build the Pipecat direct-function tools that read/write the snapshot.

    Returns ``[update_caller_snapshot, get_caller_snapshot]`` as closures over
    ``call_state``. Append these to P1's shared ``tool_functions`` list and
    register each with ``llm.register_direct_function``. The closures import
    Pipecat's ``FunctionCallParams`` lazily so this module stays importable in
    test environments without a live pipeline.
    """
    from pipecat.services.llm_service import FunctionCallParams  # local import

    call_state.setdefault("caller_snapshot", fresh_snapshot())

    async def update_caller_snapshot(
        params: FunctionCallParams,
        caller_name: str | None = None,
        relationship: str | None = None,
        reason: str | None = None,
        urgency: str | None = None,
        emotional_tone: str | None = None,
        communication_style: str | None = None,
        callback_preference: str | None = None,
        best_time: str | None = None,
        agent_handling_notes: str | None = None,
    ) -> None:
        """Record or update what you've learned about the caller. Call this as
        soon as you learn any of these — name, who they are to the owner, why
        they're calling, how urgent it is, how to reach them back. Pass only the
        fields you just learned; leave the rest empty. The result tells you how
        to adapt your speaking style to this caller.

        Args:
            caller_name: The caller's name, if given.
            relationship: Who they are to the owner (e.g. "client", "the caller's
                sister", "vendor", "unknown").
            reason: Why they're calling, in a short phrase.
            urgency: One of "low", "normal", "high", "emergency".
            emotional_tone: Optional. One of rushed/calm/confused/upset/concise/
                rambling/hesitant — only set this if you're confident; tone is
                otherwise inferred automatically from their words.
            communication_style: Optional short phrase for how they communicate.
            callback_preference: How they want to be reached back (e.g. "call this
                number", "text", "email").
            best_time: When is best to reach them (e.g. "before 4pm today").
            agent_handling_notes: Anything the owner should know about handling
                this caller.
        """
        snap = call_state.setdefault("caller_snapshot", fresh_snapshot())
        apply_snapshot_update(
            snap,
            caller_name=caller_name,
            relationship=relationship,
            reason=reason,
            urgency=urgency,
            emotional_tone=emotional_tone,
            communication_style=communication_style,
            callback_preference=callback_preference,
            best_time=best_time,
            agent_handling_notes=agent_handling_notes,
        )
        await params.result_callback(
            {
                "ok": True,
                "snapshot": public_snapshot(snap),
                "style_directive": live_style_directive(call_state),
            }
        )

    async def get_caller_snapshot(params: FunctionCallParams) -> None:
        """Read back the current caller snapshot and the recommended speaking
        style for this caller."""
        snap = call_state.setdefault("caller_snapshot", fresh_snapshot())
        await params.result_callback(
            {
                "snapshot": public_snapshot(snap),
                "summary": format_snapshot_summary(snap),
                "style_directive": live_style_directive(call_state),
            }
        )

    return [update_caller_snapshot, get_caller_snapshot]
