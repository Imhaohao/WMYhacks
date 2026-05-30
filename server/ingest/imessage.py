"""Derive owner priorities + people rules from recent iMessages (STRICT-LOCAL).

Reads the owner's own `~/Library/Messages/chat.db` READ-ONLY (requires macOS
Full Disk Access for the terminal), ranks the most active recent contacts and
extracts coarse topic keywords from inbound messages, then asks a LOCAL model
to phrase those *facts* into bullets. Nothing leaves the machine and no raw
message body is ever written to disk — only aggregate, derived summaries.

Privacy specifics:
- Opened read-only (`mode=ro`); we never write to the DB.
- Phone numbers are masked to the last 4 digits unless an alias is provided in
  `server/ingest_contacts.json` ({"+15551234821": "Sam"}). Emails show only the
  local part. Names appear only if the owner supplied them.
- Topic keywords are frequency-derived tokens, not message text, and stopwords
  are dropped. The model only polishes these facts (strict_local=True).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path

from . import persona_sections as ps
from .local_llm import summarize_to_bullets

DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"
ALIASES_PATH = Path(__file__).resolve().parent.parent / "ingest_contacts.json"

# Apple Cocoa epoch (2001-01-01 UTC) offset from Unix epoch, in seconds.
_APPLE_EPOCH_OFFSET = 978_307_200

_STOPWORDS = {
    "the", "and", "you", "for", "are", "was", "but", "not", "with", "this", "that",
    "have", "your", "they", "what", "when", "will", "can", "all", "any", "out",
    "get", "got", "just", "like", "now", "yeah", "yes", "okay", "ok", "from",
    "about", "would", "could", "should", "there", "here", "going", "want", "need",
    "know", "good", "thanks", "thank", "hey", "hi", "lol", "haha", "well", "did",
    "how", "her", "him", "she", "his", "our", "their", "been", "were", "them",
}
_WORD = re.compile(r"[a-zA-Z][a-zA-Z'']{2,}")


def _load_aliases() -> dict[str, str]:
    try:
        return {str(k): str(v) for k, v in json.loads(ALIASES_PATH.read_text()).items()}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {}


def _display_handle(handle: str, aliases: dict[str, str]) -> str:
    """De-identified label for a contact handle.

    Real names never reach the file: phones show the last 4 digits, emails show
    a stable 4-char hash (the local part is often a person's name). Supply
    `server/ingest_contacts.json` aliases to map a handle to a friendly name you
    choose deliberately.
    """
    if handle in aliases:
        return aliases[handle]
    if "@" in handle:  # email -> stable hash, never the local part (a name)
        token = hashlib.sha1(handle.encode("utf-8")).hexdigest()[:4]
        return f"contact …{token}"
    digits = re.sub(r"\D", "", handle)
    if len(digits) >= 4:
        return f"contact …{digits[-4:]}"
    return "unknown contact"


def _rel_age(seconds_ago: float) -> str:
    days = seconds_ago / 86_400
    if days < 1:
        hours = max(1, int(seconds_ago // 3600))
        return f"{hours}h ago"
    if days < 14:
        return f"{int(days)}d ago"
    return f"{int(days // 7)}w ago"


def _open_ro(db_path: Path) -> sqlite3.Connection:
    # `immutable=1` reads the main DB file directly, bypassing WAL/-shm locking.
    # chat.db is in WAL mode, and a plain `mode=ro` open fails with "unable to
    # open database file" because it can't create the shared-memory index. This
    # is the standard way to read a live Messages/Chrome/Safari SQLite DB.
    return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)


def read_recent(db_path: Path, days: int) -> list[dict]:
    """Return recent messages as dicts: {handle, is_from_me, text, secs_ago}.

    Raises sqlite3.Error if the DB can't be opened (e.g. no Full Disk Access).
    """
    now_unix = time.time()
    # `date` is ns since the Apple epoch (2001-01-01) on modern macOS.
    cutoff_apple_ns = int((now_unix - days * 86_400 - _APPLE_EPOCH_OFFSET) * 1_000_000_000)
    conn = _open_ro(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT h.id AS handle,
                   m.is_from_me AS is_from_me,
                   m.text AS text,
                   m.date AS date
            FROM message m
            JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.date >= ?
            ORDER BY m.date DESC
            """,
            (cutoff_apple_ns,),
        ).fetchall()
    finally:
        conn.close()

    out: list[dict] = []
    for r in rows:
        if not r["handle"]:
            continue
        msg_unix = r["date"] / 1_000_000_000 + _APPLE_EPOCH_OFFSET
        secs_ago = max(0.0, now_unix - msg_unix)
        out.append(
            {
                "handle": r["handle"],
                "is_from_me": bool(r["is_from_me"]),
                "text": r["text"] or "",
                "secs_ago": secs_ago,
            }
        )
    return out


def _contact_facts(rows: list[dict], aliases: dict[str, str], top: int) -> tuple[list[str], list[str]]:
    """Rank contacts by inbound recency+volume -> (facts_text_lines, fallback_bullets)."""
    inbound = [r for r in rows if not r["is_from_me"]]
    counts = Counter(r["handle"] for r in inbound)
    recency: dict[str, float] = {}
    for r in inbound:
        recency[r["handle"]] = min(recency.get(r["handle"], 1e18), r["secs_ago"])

    ranked = sorted(counts, key=lambda h: (counts[h], -recency.get(h, 1e18)), reverse=True)[:top]
    facts, fallback = [], []
    for h in ranked:
        label = _display_handle(h, aliases)
        line = f"{label}: {counts[h]} recent messages, last {_rel_age(recency[h])}"
        facts.append(line)
        fallback.append(f"- {line}")
    return facts, fallback


def _topic_facts(rows: list[dict], top: int) -> tuple[list[str], list[str]]:
    """Coarse inbound topic keywords -> (facts_lines, fallback_bullets)."""
    tokens: Counter = Counter()
    for r in rows:
        if r["is_from_me"] or not r["text"]:
            continue
        for w in _WORD.findall(r["text"].lower()):
            if w not in _STOPWORDS:
                tokens[w] += 1
    common = [w for w, _ in tokens.most_common(top)]
    if not common:
        return [], []
    facts = [", ".join(common)]
    fallback = [f"- Recent inbound topics mention: {', '.join(common)}."]
    return facts, fallback


def ingest(
    days: int = 14,
    *,
    db_path: Path = DEFAULT_DB,
    top_contacts: int = 5,
    top_topics: int = 8,
    dry_run: bool = False,
) -> dict:
    """Read iMessages, derive blocks, and write them into persona_context.md."""
    report: dict = {"source": "imessage", "status": "skipped", "blocks": []}

    if not db_path.exists():
        report["reason"] = f"chat.db not found at {db_path}"
        return report
    try:
        rows = read_recent(db_path, days)
    except sqlite3.Error as e:
        report["reason"] = (
            f"cannot read chat.db ({e}). Grant Full Disk Access to your terminal "
            "in System Settings → Privacy & Security → Full Disk Access."
        )
        return report

    if not rows:
        report["reason"] = f"no messages in the last {days} days"
        report["status"] = "ok"
        return report

    aliases = _load_aliases()
    contact_facts, contact_fallback = _contact_facts(rows, aliases, top_contacts)
    topic_facts, topic_fallback = _topic_facts(rows, top_topics)

    # People Rules: who is active and likely expecting a callback.
    people = summarize_to_bullets(
        "\n".join(contact_facts) or "(no recent contacts)",
        "From these recently-active contacts, write people-handling rules for a "
        "voicemail assistant: who to treat warmly / likely expecting a callback. "
        "Refer to people only by the given labels.",
        contact_fallback or ["- No standout recent contacts."],
        strict_local=True,
        max_items=top_contacts,
    )
    # Current Priorities: themes the owner is currently dealing with.
    priorities = summarize_to_bullets(
        "\n".join(topic_facts) or "(no clear topics)",
        "From these recurring inbound topic keywords, infer what the owner is "
        "currently focused on or waiting on. Keep it to a few neutral bullets.",
        topic_fallback or ["- No strong recent topics detected."],
        strict_local=True,
        max_items=4,
    )

    engine = people.engine if people.engine != "deterministic" else priorities.engine

    # Distinct block ids per section: apply_block locates a fence by id across
    # the whole document, so a shared id would make the second write clobber the
    # first instead of creating its own block.
    for section, block_id, res in (
        ("Current Priorities", "imessage_priorities", priorities),
        ("People Rules", "imessage_people", people),
    ):
        changed, _ = ps.update_file(section, block_id, res.lines, dry_run=dry_run)
        report["blocks"].append(
            {"section": section, "block_id": block_id, "lines": res.lines, "changed": changed}
        )

    report["status"] = "ok"
    report["engine"] = engine
    report["stats"] = {
        "messages_seen": len(rows),
        "contacts_ranked": len(contact_fallback),
        "days": days,
    }
    return report


if __name__ == "__main__":
    import pprint

    pprint.pp(ingest(dry_run=True))
