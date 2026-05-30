#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Standalone end-to-end harness for Person 3's slices (Part A + B + C).

Exercises the caller snapshot, action layer, and AWS persistence WITHOUT the
live Pipecat bot — so these pieces can be demoed and tested before P1's frozen
``call_state`` lands. Once P1 pushes, the exact same calls happen inside
``run_bot`` (see INTEGRATION_P3.md); this file just stands in for the pipeline.

Run:
    uv run python demo_p3.py
"""

from __future__ import annotations

import asyncio
from typing import Any

import actions
import caller_snapshot as cs
import persistence


class _FakeParams:
    """Minimal stand-in for Pipecat's FunctionCallParams: just an async
    result_callback that records the last tool result."""

    def __init__(self) -> None:
        self.last: dict[str, Any] | None = None

    async def result_callback(self, result: dict[str, Any], properties: Any = None) -> None:
        self.last = result


def fresh_call_state() -> dict[str, Any]:
    """Mirror of P1's anticipated frozen call_state shape."""
    return {
        "voicemail": {},  # P1 fills structured fields
        "caller_snapshot": cs.fresh_snapshot(),  # P3 — fresh per call
        "persona_context": "",  # P2 injects
        "actions": [],  # P3 action layer appends
    }


async def main() -> None:
    print("=" * 64)
    print("Person 3 end-to-end demo — snapshot + actions + persistence")
    print("=" * 64)

    call_state = fresh_call_state()
    snap_tools = cs.make_snapshot_tools(call_state)
    update_caller_snapshot, get_caller_snapshot = snap_tools
    send_owner_email, book_callback_slot = actions.make_action_tools(call_state)

    # --- Simulate the caller's turns; tone is inferred from WORDING only -----
    transcript = [
        "Hey, hi, sorry — I'm kind of in a rush here, gotta run soon.",
        "It's Sam, I'm one of the judges. Can they call me before 4 today?",
        "Yeah just call this number back, that's the fastest. Thanks!",
    ]
    print("\n--- caller turns (tone inferred live) ---")
    for utt in transcript:
        tone = cs.observe_caller_tone(call_state["caller_snapshot"], utt)
        print(f"  tone={tone:8} :: {utt}")
        print(f"           -> {cs.live_style_directive(call_state)}")

    # --- LLM 'calls' the snapshot tool as it learns facts --------------------
    p = _FakeParams()
    await update_caller_snapshot(
        p,
        caller_name="Sam",
        relationship="judge",
        reason="needs a callback about the judging schedule",
        urgency="high",
        callback_preference="call back on this number",
        best_time="before 4pm today",
        agent_handling_notes="Time-sensitive; keep it brief, they're rushed.",
    )
    print("\n--- update_caller_snapshot result ---")
    print("  style_directive:", (p.last or {}).get("style_directive"))

    # P1 would normally fill these; stub structured voicemail fields here.
    call_state["voicemail"] = {
        "caller_name": "Sam",
        "reason": "callback about the judging schedule",
        "urgency": "high",
        "callback_preference": "call back on this number",
        "best_time": "before 4pm today",
        "message": "Sam (judge) needs a callback before 4pm about the judging schedule.",
    }

    # --- Action layer: email the owner + book a tentative callback slot -------
    print("\n--- action layer ---")
    pe = _FakeParams()
    await send_owner_email(pe)
    print("  email:", pe.last)

    pc = _FakeParams()
    await book_callback_slot(
        pc, requested_time="before 4pm today", title="Callback: Sam (judge)"
    )
    print("  calendar:", pc.last)

    # --- Final summary includes the snapshot (Part A, task 4) ----------------
    print("\n--- final voicemail summary ---")
    print(actions.build_summary_text(call_state))

    # --- Persist the finished voicemail (Part C) -----------------------------
    print("\n--- persistence ---")
    record = persistence.persist_voicemail(
        voicemail=call_state["voicemail"],
        caller_snapshot=call_state["caller_snapshot"],
        actions_taken=call_state["actions"],
        owner_context_tag="field-and-flower",
    )
    print(f"  backend={persistence.backend_kind()}  stored_at={record['_stored_at']}")
    print(f"  record_id={record['record_id']}")
    print(f"  actions_taken={len(record['actions_taken'])}")

    print("\nDone. Show the persisted record with:")
    print("  uv run python persistence.py --show --type voicemail")
    print("Drain queued email/calendar actions with:")
    print("  uv run python action_bridge.py --list")


if __name__ == "__main__":
    asyncio.run(main())
