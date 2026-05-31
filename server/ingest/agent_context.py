"""Distill how the owner prompts & delegates, from past Claude sessions.

Reads `~/.claude/projects/**/*.jsonl` (the owner's own session transcripts),
extracts the owner's user-role prompts, and derives a short profile of their
working style — concise vs verbose, what they delegate, recurring domains. This
is the "learns" story: the voicemail proxy inherits the owner's communication
style. Derived only; raw prompts are never written to disk.

Local-preferred (strict_local=True by default) since prompts can contain
project detail.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path

from . import memo_context
from . import persona_sections as ps
from .local_llm import summarize_to_bullets

DEFAULT_ROOT = Path.home() / ".claude" / "projects"

_DELEGATION_SIGNALS = (
    "delegate", "subagent", "sub-agent", "in parallel", "parallel", "background",
    "haiku", "sonnet", "opus", "ollama", "plan mode", "spawn", "agent",
    "concise", "don't", "do not", "make sure", "verify",
)
_WORD = re.compile(r"[a-zA-Z][a-zA-Z'']{2,}")
_STOP = {
    "the", "and", "you", "for", "are", "this", "that", "with", "have", "your",
    "what", "when", "can", "all", "use", "should", "would", "could", "into",
    "from", "then", "also", "but", "not", "get", "now", "want", "need", "make",
    "let", "add", "run", "see", "like", "out", "any", "how", "why", "one",
}


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(parts)
    return ""


def _is_owner_prompt(text: str) -> bool:
    if not text or len(text.strip()) < 4:
        return False
    t = text.lstrip()
    # Skip harness-injected / command wrapper content, not authored by the owner.
    return not t.startswith(("<command-", "<local-command", "Caveat:", "[Request interrupted"))


def _iter_user_prompts(jsonl_path: Path):
    try:
        with jsonl_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "user":
                    continue
                msg = rec.get("message") or {}
                if msg.get("role") != "user":
                    continue
                text = _extract_text(msg.get("content")).strip()
                if _is_owner_prompt(text):
                    yield text
    except (FileNotFoundError, OSError):
        return


def read_prompts(root: Path = DEFAULT_ROOT, max_files: int = 15, max_prompts: int = 500) -> list[str]:
    """Most-recent owner prompts across recent session files."""
    files = sorted(root.glob("**/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    prompts: list[str] = []
    for f in files[:max_files]:
        for text in _iter_user_prompts(f):
            prompts.append(text)
            if len(prompts) >= max_prompts:
                return prompts
    return prompts


def _derive_facts(prompts: list[str]) -> tuple[str, list[str]]:
    if not prompts:
        return "", []
    lengths = [len(p.split()) for p in prompts]
    # Median, not mean: a few giant pasted briefs would otherwise dominate and
    # mislabel a concise user as "detailed".
    typical = statistics.median(lengths)
    brevity = "very concise" if typical < 12 else "moderate" if typical < 40 else "detailed"

    joined = " ".join(prompts).lower()
    signals = [s for s in _DELEGATION_SIGNALS if s in joined]

    tokens: Counter = Counter()
    for p in prompts:
        for w in _WORD.findall(p.lower()):
            if w not in _STOP:
                tokens[w] += 1
    domains = [w for w, _ in tokens.most_common(10)]

    facts = (
        f"prompt count: {len(prompts)}\n"
        f"typical prompt length: {typical:.0f} words ({brevity})\n"
        f"delegation / style signals present: {', '.join(signals) or 'none'}\n"
        f"recurring domain words: {', '.join(domains)}"
    )
    fallback = [
        f"- Communicates in a {brevity} style (~{typical:.0f} words per request, median).",
    ]
    if signals:
        fallback.append(f"- Often delegates / directs explicitly: {', '.join(signals[:6])}.")
    if domains:
        fallback.append(f"- Recurring focus areas: {', '.join(domains[:6])}.")
    return facts, fallback


def ingest(
    *,
    root: Path = DEFAULT_ROOT,
    strict_local: bool = True,
    dry_run: bool = False,
) -> dict:
    report: dict = {"source": "agent", "status": "skipped", "blocks": []}
    if not root.exists():
        report["reason"] = f"no Claude session logs at {root}"
        return report

    prompts = read_prompts(root)
    if not prompts:
        report["reason"] = "no owner prompts found in session logs"
        report["status"] = "ok"
        return report

    facts, fallback = _derive_facts(prompts)
    res = summarize_to_bullets(
        facts,
        "Describe how this person prefers to work and communicate, so an "
        "assistant can mirror their tone. Focus on brevity, directness, and what "
        "they delegate. Keep it to a few bullets.",
        fallback,
        strict_local=strict_local,
        max_items=4,
    )
    changed, _ = ps.update_file("Recent Agent Context", "agent", res.lines, dry_run=dry_run)
    report["blocks"].append(
        {"section": "Recent Agent Context", "block_id": "agent", "lines": res.lines, "changed": changed}
    )
    lingo_lines = memo_context.derive_prompt_lingo_lines(prompts, "Agent prompt")
    changed, _ = ps.update_optional_file("Persona", "agent_lingo", lingo_lines, dry_run=dry_run)
    report["blocks"].append(
        {
            "section": "Persona",
            "block_id": "agent_lingo",
            "lines": lingo_lines,
            "changed": changed,
        }
    )
    report["status"] = "ok"
    report["engine"] = res.engine
    report["stats"] = {"prompts_seen": len(prompts)}
    return report


if __name__ == "__main__":
    import pprint

    pprint.pp(ingest(dry_run=True))
