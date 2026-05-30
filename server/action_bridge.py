#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Outbox bridge: turn queued actions into real Gmail / Calendar effects.

The bot process can't reach the agent-session MCP connectors, so
``actions.py`` queues email/calendar requests to an outbox JSONL. This bridge
reads that outbox and, for each *pending* action, prints the exact connector
call to make. Whoever holds the live MCP session (the agent on stage, or a
person) fires it, then marks it fulfilled here.

Fulfillment mapping
-------------------
* ``send_email``   → Gmail ``create_draft(to=[OWNER], subject, body)``.
  NOTE: the Gmail connector only exposes *create_draft*, not send. For an email
  that actually lands in the owner's INBOX, set ``GMAIL_APP_PASSWORD`` +
  ``OWNER_EMAIL`` and ``actions.py`` sends via SMTP directly (no bridge needed).
* ``create_event`` → Calendar ``create_event(summary, startTime, endTime, ...)``.
  Tentativeness is encoded in the title ("[Tentative] …"), a Graphite color,
  and a description note, since the connector's create_event has no status arg.

Usage:
    uv run python action_bridge.py --list           # show pending actions
    uv run python action_bridge.py --emit            # print connector call specs
    uv run python action_bridge.py --done <action_id>  # mark one fulfilled
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_OUTBOX = Path(__file__).parent / "outbox" / "actions.jsonl"
_DEFAULT_FULFILLED = Path(__file__).parent / "outbox" / "fulfilled.jsonl"

TENTATIVE_COLOR_ID = "8"  # Graphite — visually reads as "soft / proposed"


def _outbox_path() -> Path:
    return Path(os.getenv("PERSIST_OUTBOX_PATH", str(_DEFAULT_OUTBOX)))


def _fulfilled_path() -> Path:
    return Path(os.getenv("PERSIST_FULFILLED_PATH", str(_DEFAULT_FULFILLED)))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _fulfilled_ids() -> set[str]:
    return {str(aid) for r in _read_jsonl(_fulfilled_path()) if (aid := r.get("action_id"))}


def pending_actions() -> list[dict[str, Any]]:
    done = _fulfilled_ids()
    return [a for a in _read_jsonl(_outbox_path()) if a.get("action_id") not in done]


def mark_done(action_id: str, result: dict[str, Any] | None = None) -> None:
    path = _fulfilled_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "action_id": action_id,
                    "fulfilled_at": datetime.now(UTC).isoformat(),
                    "result": result or {},
                },
                default=str,
            )
            + "\n"
        )


def connector_spec(action: dict[str, Any]) -> dict[str, Any]:
    """Translate one queued action into a ready-to-fire connector call spec."""
    kind = action.get("action")
    if kind == "send_email":
        return {
            "action_id": action.get("action_id"),
            "connector": "gmail.create_draft",
            "args": {
                "to": [action.get("to")],
                "subject": action.get("subject"),
                "body": action.get("body"),
            },
            "note": "Draft only. For real inbox delivery use SMTP (GMAIL_APP_PASSWORD).",
        }
    if kind == "create_event":
        title = action.get("title") or "Callback"
        return {
            "action_id": action.get("action_id"),
            "connector": "calendar.create_event",
            "args": {
                "summary": f"[Tentative] {title}",
                "startTime": action.get("start_iso"),
                "endTime": action.get("end_iso"),
                "description": (
                    "TENTATIVE — proposed callback from a voicemail; confirm before relying "
                    f"on it.\nRequested: {action.get('requested_time')}\n\n"
                    + (action.get("description") or "")
                ),
                "colorId": TENTATIVE_COLOR_ID,
                "visibility": "private",
            },
            "needs": (
                None
                if action.get("start_iso")
                else "start_iso/end_iso unresolved — resolve from requested_time before firing"
            ),
        }
    return {"action_id": action.get("action_id"), "connector": "unknown", "raw": action}


def _main() -> None:
    parser = argparse.ArgumentParser(description="Drain the action outbox into connectors")
    parser.add_argument("--list", action="store_true", help="list pending actions")
    parser.add_argument("--emit", action="store_true", help="print connector call specs")
    parser.add_argument("--done", metavar="ACTION_ID", help="mark an action fulfilled")
    args = parser.parse_args()

    if args.done:
        mark_done(args.done)
        print(f"Marked {args.done} fulfilled.")
        return

    pending = pending_actions()
    print(f"{len(pending)} pending action(s) in {_outbox_path()}\n")
    for a in pending:
        if args.emit:
            print(json.dumps(connector_spec(a), indent=2, default=str))
        else:
            print(
                f"  [{a.get('action_id', '?')[:8]}] {a.get('action'):12} "
                f"-> {a.get('to') or a.get('calendar')}  status={a.get('status')}"
            )
        print("-" * 60)


if __name__ == "__main__":
    _main()
