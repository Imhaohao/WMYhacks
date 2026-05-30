"""Redacted message digest — the P2 seam for Gate C (UI_CONTEXT_PLAN.md).

The voice bot only ever sees a *redacted* summary of recent threads: topics and
urgency, never raw message bodies. This module is the single seam P2 replaces
when live iMessage ingestion lands. Until then it reads an optional, owner-curated
redacted fixture so the setup flow and demo have something to show.

Privacy invariants (must hold for any future P2 implementation):
  - Return topics / urgency only. Never return raw chat bodies.
  - Never persist raw messages anywhere reachable by the API or the prompt.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

import owner_config

# Optional redacted digest source. P2's live ingestion will replace
# build_message_digest() entirely; this fixture only exists so the manual /
# demo path has content. It must contain topics/urgency only — no raw bodies.
DIGEST_SOURCE_PATH = Path(__file__).parent / "message_digest.json"

# Keep parity with the persona-context soft cap so a digest can't blow the budget.
_DIGEST_LIMIT = 1000


def _format_entries(entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for entry in entries:
        who = str(entry.get("who") or "").strip()
        topic = str(entry.get("topic") or "").strip()
        urgency = str(entry.get("urgency") or "").strip()
        if not topic:
            continue
        head = f"{who} — {topic}" if who else topic
        lines.append(f"{head} ({urgency})" if urgency else head)
    return "; ".join(lines)


def build_message_digest() -> str:
    """Return a redacted digest of recent threads (topics + urgency only).

    P2 overrides this with live iMessage ingestion. The default reads an optional
    redacted fixture at ``message_digest.json``:

        {"entries": [{"who": "Sarah", "topic": "lease renewal", "urgency": "waiting on reply"}]}

    Returns ``""`` when no source is available (the bot then falls back to whatever
    the owner typed manually).
    """
    if not DIGEST_SOURCE_PATH.exists():
        return ""
    try:
        data = json.loads(DIGEST_SOURCE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return ""
    digest = _format_entries([e for e in entries if isinstance(e, dict)])
    if len(digest) > _DIGEST_LIMIT:
        digest = digest[: _DIGEST_LIMIT - 1].rstrip() + "…"
    return digest


def sync_message_context(*, force: bool = True) -> dict[str, Any]:
    """Refresh ``message_context_summary`` from the digest producer.

    The owner's manual edit wins until an *explicit* re-sync: an auto/cron caller
    passes ``force=False`` and is skipped while the current source is ``"manual"``.
    The explicit ``POST /api/context/sync-messages`` path passes ``force=True``.

    Returns the persisted message-context fields (no raw messages).
    """
    cfg = owner_config.load_owner_config()
    current_source = cfg.get("message_context_source") or "manual"
    if not force and current_source == "manual" and cfg.get("message_context_summary"):
        return _message_context_view(cfg)

    digest = build_message_digest()
    saved = owner_config.save_owner_config(
        {
            "message_context_summary": digest,
            "message_context_source": "imessage",
            "message_context_synced_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
    )
    return _message_context_view(saved)


def _message_context_view(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "message_context_summary": cfg.get("message_context_summary", ""),
        "message_context_source": cfg.get("message_context_source", "manual"),
        "message_context_synced_at": cfg.get("message_context_synced_at", ""),
    }
