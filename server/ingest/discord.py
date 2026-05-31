"""Derive the owner's writing voice + recent context from a Discord export.

Discord has no live local DB like iMessage, so this reads a *file the owner
exported themselves* and derives summaries from it — never live-scraping. Two
export formats are supported:

1. **DiscordChatExporter JSON** (recommended; https://github.com/Tyrrrz/DiscordChatExporter)
   — a `{"messages": [{author, content, timestamp}, ...]}` object, one per
   channel/DM. Carries per-message authorship, so the owner's *outbound* voice
   can be separated from inbound context.
2. **Official Discord data export** — the `messages/` folder of `messages.csv`
   (or `messages.json`) files Discord emails you on request. These contain ONLY
   the owner's own messages, so every row is treated as outbound.

What it derives (each written to a fenced block, like iMessage):
- **Persona** ← the owner's OUTBOUND voice: typical length, casual markers,
  recurring vocabulary — the "personality" the proxy should mirror.
- **Current Priorities** ← recent INBOUND topic keywords (what people are
  messaging the owner about).
- **People Rules** ← most-active recent contacts (who's likely expecting a reply).

Privacy mirrors `imessage.py` exactly:
- Only DERIVED, aggregate summaries are written — never a raw message body.
- Summarized STRICT-LOCAL (local Ollama or a deterministic extractor); Discord
  content never reaches a cloud model.
- Other people's usernames are de-identified to a stable hashed token unless the
  owner supplies a deliberate alias in `server/ingest_contacts.json`.

Point it at an export with `--discord-path` (default `server/discord_export.json`)
and tell it which author is you with `DISCORD_OWNER` (your Discord user id or
username); without that, every message is treated as context-only and the voice
block is skipped.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path

from . import persona_sections as ps
from .local_llm import summarize_to_bullets

DEFAULT_EXPORT = Path(__file__).resolve().parent.parent / "discord_export.json"
ALIASES_PATH = Path(__file__).resolve().parent.parent / "ingest_contacts.json"

# Stopwords shared in spirit with imessage.py; kept local so the modules stay
# independent.
_STOPWORDS = {
    "the", "and", "you", "for", "are", "was", "but", "not", "with", "this", "that",
    "have", "your", "they", "what", "when", "will", "can", "all", "any", "out",
    "get", "got", "just", "like", "now", "yeah", "yes", "okay", "ok", "from",
    "about", "would", "could", "should", "there", "here", "going", "want", "need",
    "know", "good", "thanks", "thank", "hey", "hi", "lol", "haha", "well", "did",
    "how", "her", "him", "she", "his", "our", "their", "been", "were", "them",
}
_WORD = re.compile(r"[a-zA-Z][a-zA-Z'']{2,}")
# Casual interjections that characterize an informal chat voice.
_CASUAL_MARKERS = (
    "lol", "lmao", "lmfao", "haha", "hahaha", "ngl", "tbh", "fr", "imo", "idk",
    "omg", "bruh", "yeah", "yep", "nah", "ok", "okay", "gonna", "wanna", "ya",
)
_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")   # <:name:id> / <a:name:id>
_SHORTCODE = re.compile(r":[a-z0-9_+-]+:")     # :smile:


@dataclass
class Msg:
    author_id: str
    author_name: str
    is_from_me: bool
    text: str
    secs_ago: float


def _load_aliases() -> dict[str, str]:
    try:
        return {str(k): str(v) for k, v in json.loads(ALIASES_PATH.read_text()).items()}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {}


def _display_author(author_id: str, author_name: str, aliases: dict[str, str]) -> str:
    """De-identified label for a contact.

    Real usernames never reach the file. An alias keyed by the Discord user id
    or username (in `ingest_contacts.json`) wins — that's a deliberate,
    owner-chosen name. Otherwise we emit a stable 4-char hash so the same person
    reads consistently across runs without exposing their handle.
    """
    if author_id and author_id in aliases:
        return aliases[author_id]
    if author_name and author_name in aliases:
        return aliases[author_name]
    seed = author_id or author_name or "unknown"
    token = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:4]
    return f"user …{token}"


def _now_unix() -> float:
    return datetime.now(UTC).timestamp()


def _parse_ts(raw: str, now: float) -> float | None:
    """ISO-8601 timestamp -> seconds-ago, or None if unparseable."""
    if not raw:
        return None
    s = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max(0.0, now - dt.timestamp())


def _is_owner(author_id: str, author_name: str, owner: str | None) -> bool:
    if not owner:
        return False
    owner = owner.strip()
    return owner == author_id or owner.lower() == (author_name or "").lower()


def _parse_dce_obj(obj: dict, owner: str | None, now: float) -> list[Msg]:
    """Parse one DiscordChatExporter JSON object ({'messages': [...]})."""
    out: list[Msg] = []
    for m in obj.get("messages") or []:
        if not isinstance(m, dict):
            continue
        text = (m.get("content") or "").strip()
        if not text:  # attachment-only / system messages carry no voice signal
            continue
        author = m.get("author") or {}
        aid = str(author.get("id") or "")
        aname = str(author.get("nickname") or author.get("name") or "")
        secs = _parse_ts(str(m.get("timestamp") or ""), now)
        if secs is None:
            continue
        out.append(Msg(aid, aname, _is_owner(aid, aname, owner), text, secs))
    return out


def _parse_official_rows(rows, now: float) -> list[Msg]:
    """Parse rows from the official export (csv.DictReader or list of dicts).

    The official package contains only the owner's own messages, so every row is
    outbound. Columns are `ID, Timestamp, Contents, Attachments` (CSV) or the
    same keys in JSON.
    """
    out: list[Msg] = []
    for r in rows:
        text = (r.get("Contents") or r.get("contents") or "").strip()
        if not text:
            continue
        secs = _parse_ts(str(r.get("Timestamp") or r.get("timestamp") or ""), now)
        if secs is None:
            continue
        out.append(Msg("", "", True, text, secs))
    return out


def _load_one_file(path: Path, owner: str | None, now: float) -> list[Msg]:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            with path.open(encoding="utf-8", newline="") as f:
                return _parse_official_rows(csv.DictReader(f), now)
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "messages" in data:
                return _parse_dce_obj(data, owner, now)
            if isinstance(data, list):  # official messages.json: a flat array
                return _parse_official_rows(data, now)
    except (OSError, json.JSONDecodeError, csv.Error, UnicodeDecodeError):
        return []
    return []


def load_export(path: Path, owner: str | None = None) -> list[Msg]:
    """Load messages from a file or a directory of exports.

    A directory is walked for `*.json` and `*.csv` (the official package nests
    one `messages.csv` per channel under `messages/`).
    """
    now = _now_unix()
    if path.is_dir():
        out: list[Msg] = []
        for f in sorted(path.glob("**/*.json")) + sorted(path.glob("**/*.csv")):
            out.extend(_load_one_file(f, owner, now))
        return out
    return _load_one_file(path, owner, now)


def _rel_age(seconds_ago: float) -> str:
    days = seconds_ago / 86_400
    if days < 1:
        return f"{max(1, int(seconds_ago // 3600))}h ago"
    if days < 14:
        return f"{int(days)}d ago"
    return f"{int(days // 7)}w ago"


def _style_facts(outbound: list[Msg]) -> tuple[list[str], list[str]]:
    """Aggregate voice signals from the owner's OWN messages -> (facts, fallback).

    All signals are counts/frequencies over the corpus, never raw text, so the
    owner's words are characterized without being quoted.
    """
    if not outbound:
        return [], []
    import statistics

    lengths = [len(m.text.split()) for m in outbound]
    typical = statistics.median(lengths)
    brevity = "very short" if typical < 6 else "short" if typical < 16 else "medium-length"

    n = len(outbound)
    lower_only = sum(1 for m in outbound if m.text == m.text.lower()) / n
    has_excl = sum(1 for m in outbound if "!" in m.text) / n
    has_q = sum(1 for m in outbound if "?" in m.text) / n
    uses_emoji = sum(
        1 for m in outbound if _CUSTOM_EMOJI.search(m.text) or _SHORTCODE.search(m.text)
    ) / n

    joined = " ".join(m.text.lower() for m in outbound)
    markers = [w for w in _CASUAL_MARKERS if re.search(rf"\b{re.escape(w)}\b", joined)]

    tokens: Counter = Counter()
    for m in outbound:
        for w in _WORD.findall(m.text.lower()):
            if w not in _STOPWORDS:
                tokens[w] += 1
    vocab = [w for w, _ in tokens.most_common(8)]

    facts = [
        f"messages written by owner: {n}",
        f"typical message length: {typical:.0f} words ({brevity})",
        f"all-lowercase messages: {lower_only:.0%}; uses '!': {has_excl:.0%}; "
        f"asks questions: {has_q:.0%}; uses emoji/reactions: {uses_emoji:.0%}",
        f"casual markers used: {', '.join(markers) or 'none'}",
        f"recurring vocabulary: {', '.join(vocab) or 'n/a'}",
    ]
    fallback = [
        f"- Writes {brevity} messages (~{typical:.0f} words, median).",
    ]
    tone_bits = []
    if lower_only > 0.5:
        tone_bits.append("usually lowercase")
    if has_excl > 0.3:
        tone_bits.append("uses exclamation points")
    if uses_emoji > 0.2:
        tone_bits.append("uses emoji")
    if markers:
        tone_bits.append(f"casual ({', '.join(markers[:4])})")
    if tone_bits:
        fallback.append(f"- Informal tone: {', '.join(tone_bits)}.")
    if vocab:
        fallback.append(f"- Recurring words: {', '.join(vocab[:6])}.")
    return facts, fallback


def _topic_facts(inbound: list[Msg], top: int) -> tuple[list[str], list[str]]:
    tokens: Counter = Counter()
    for m in inbound:
        for w in _WORD.findall(m.text.lower()):
            if w not in _STOPWORDS:
                tokens[w] += 1
    common = [w for w, _ in tokens.most_common(top)]
    if not common:
        return [], []
    return [", ".join(common)], [f"- Recent Discord topics mention: {', '.join(common)}."]


def _people_facts(inbound: list[Msg], aliases: dict[str, str], top: int) -> tuple[list[str], list[str]]:
    keyed = [m for m in inbound if (m.author_id or m.author_name)]
    counts: Counter = Counter((m.author_id or m.author_name) for m in keyed)
    recency: dict[str, float] = {}
    label_of: dict[str, str] = {}
    for m in keyed:
        key = m.author_id or m.author_name
        recency[key] = min(recency.get(key, 1e18), m.secs_ago)
        label_of[key] = _display_author(m.author_id, m.author_name, aliases)

    ranked = sorted(counts, key=lambda k: (counts[k], -recency.get(k, 1e18)), reverse=True)[:top]
    facts, fallback = [], []
    for k in ranked:
        line = f"{label_of[k]}: {counts[k]} recent messages, last {_rel_age(recency[k])}"
        facts.append(line)
        fallback.append(f"- {line}")
    return facts, fallback


def ingest(
    days: int = 30,
    *,
    export_path: Path = DEFAULT_EXPORT,
    owner: str | None = None,
    top_contacts: int = 5,
    top_topics: int = 8,
    dry_run: bool = False,
) -> dict:
    """Read a Discord export, derive blocks, and write them to persona_context.md.

    `owner` (defaults to the DISCORD_OWNER env var) is the owner's Discord user
    id or username — used to separate the owner's outbound voice from inbound
    context. Style is derived over ALL outbound messages (a personal voice is
    stable); topics/people use only the last `days`.
    """
    owner = owner if owner is not None else os.getenv("DISCORD_OWNER")
    report: dict = {"source": "discord", "status": "skipped", "blocks": []}

    if not export_path.exists():
        report["reason"] = (
            f"no export at {export_path}. Export a channel/DM with DiscordChatExporter "
            "(-f Json) or request your official data, then pass --discord-path."
        )
        return report

    msgs = load_export(export_path, owner)
    if not msgs:
        report["reason"] = f"no messages parsed from {export_path} (unrecognized format or empty)"
        report["status"] = "ok"
        return report

    aliases = _load_aliases()
    outbound = [m for m in msgs if m.is_from_me]
    windowed = [m for m in msgs if m.secs_ago <= days * 86_400]
    inbound_window = [m for m in windowed if not m.is_from_me]

    engine = "deterministic"

    # Persona <- the owner's own voice (the "personality" goal). Only emitted
    # when we could actually identify the owner's messages.
    if outbound:
        style_facts, style_fallback = _style_facts(outbound)
        style = summarize_to_bullets(
            "\n".join(style_facts),
            "From these aggregate writing-style signals, describe how this person "
            "writes in chat — length, formality, tone, habits — so a voice assistant "
            "can mirror their personality. Do not quote messages. A few bullets.",
            style_fallback,
            strict_local=True,
            max_items=4,
        )
        engine = style.engine
        changed, _ = ps.update_file("Persona", "discord_style", style.lines, dry_run=dry_run)
        report["blocks"].append(
            {"section": "Persona", "block_id": "discord_style", "lines": style.lines, "changed": changed}
        )
    else:
        report["voice_skipped"] = (
            "no owner messages identified — set DISCORD_OWNER to your Discord user id "
            "or username (DiscordChatExporter exports), or use your official data export"
        )

    # Current Priorities <- recent inbound topics.
    topic_facts, topic_fallback = _topic_facts(inbound_window, top_topics)
    if topic_facts:
        topics = summarize_to_bullets(
            "\n".join(topic_facts),
            "From these recurring Discord topic keywords, infer what the owner is "
            "currently discussing or following. A few neutral bullets.",
            topic_fallback,
            strict_local=True,
            max_items=4,
        )
        engine = topics.engine if engine == "deterministic" else engine
        changed, _ = ps.update_file("Current Priorities", "discord_topics", topics.lines, dry_run=dry_run)
        report["blocks"].append(
            {"section": "Current Priorities", "block_id": "discord_topics", "lines": topics.lines, "changed": changed}
        )

    # People Rules <- recently-active contacts.
    people_facts, people_fallback = _people_facts(inbound_window, aliases, top_contacts)
    if people_facts:
        people = summarize_to_bullets(
            "\n".join(people_facts),
            "From these recently-active Discord contacts, write people-handling "
            "rules for a voicemail assistant: who is active and likely expecting a "
            "reply. Refer to people only by the given labels.",
            people_fallback,
            strict_local=True,
            max_items=top_contacts,
        )
        engine = people.engine if engine == "deterministic" else engine
        changed, _ = ps.update_file("People Rules", "discord_people", people.lines, dry_run=dry_run)
        report["blocks"].append(
            {"section": "People Rules", "block_id": "discord_people", "lines": people.lines, "changed": changed}
        )

    report["status"] = "ok"
    report["engine"] = engine
    report["stats"] = {
        "messages_seen": len(msgs),
        "owner_messages": len(outbound),
        "inbound_in_window": len(inbound_window),
        "days": days,
    }
    return report


if __name__ == "__main__":
    import pprint

    pprint.pp(ingest(dry_run=True))
