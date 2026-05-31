"""Distill owner working style from local Codex thread metadata.

Reads the owner's local Codex SQLite state read-only and extracts only
first-user-message prompts. Raw prompts are reduced locally to coarse aggregate
signals before the strict-local summarizer sees them. Only derived bullets are
written to persona_context.md.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import memo_context
from . import persona_sections as ps
from .local_llm import summarize_to_bullets

DEFAULT_DB = Path.home() / ".codex" / "state_5.sqlite"


def _open_ro(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)


def read_prompts(db_path: Path = DEFAULT_DB, max_threads: int = 100) -> list[str]:
    """Return recent first user messages from the local Codex thread index."""
    conn = _open_ro(db_path)
    try:
        rows = conn.execute(
            """
            SELECT first_user_message
            FROM threads
            WHERE first_user_message <> ''
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max_threads,),
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]).strip() for row in rows if row[0] and str(row[0]).strip()]


def ingest(*, db_path: Path = DEFAULT_DB, dry_run: bool = False) -> dict:
    report: dict = {"source": "codex", "status": "skipped", "blocks": []}
    if not db_path.exists():
        report["reason"] = f"Codex state DB not found at {db_path}"
        return report
    try:
        prompts = read_prompts(db_path)
    except sqlite3.Error as exc:
        report["reason"] = f"cannot read Codex state DB ({exc})"
        return report
    if not prompts:
        report["reason"] = "no owner prompts found in Codex thread metadata"
        report["status"] = "ok"
        return report

    facts, fallback = memo_context.derive_prompt_facts(prompts)
    res = summarize_to_bullets(
        facts,
        "Describe how this person prefers to work and communicate. Focus on "
        "brevity, directness, and workflow habits. Keep it to a few bullets.",
        fallback,
        strict_local=True,
        max_items=3,
    )
    changed, _ = ps.update_file("Recent Agent Context", "codex", res.lines, dry_run=dry_run)
    report["blocks"].append(
        {"section": "Recent Agent Context", "block_id": "codex", "lines": res.lines, "changed": changed}
    )
    lingo_lines = memo_context.derive_prompt_lingo_lines(prompts, "Codex prompt")
    changed, _ = ps.update_optional_file("Persona", "codex_lingo", lingo_lines, dry_run=dry_run)
    report["blocks"].append(
        {
            "section": "Persona",
            "block_id": "codex_lingo",
            "lines": lingo_lines,
            "changed": changed,
        }
    )
    report["status"] = "ok"
    report["engine"] = res.engine
    report["stats"] = {"prompts_seen": len(prompts)}
    return report
