"""Hermetic tests for Part B ingestion (no network, no real user data).

Run from server/:  uv run pytest ingest/test_ingest.py -q
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

# Allow `from ingest ...` when pytest is run from server/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import agent_context, gcal, imessage, local_llm  # noqa: E402
from ingest import persona_sections as ps  # noqa: E402

# --- persona_sections ----------------------------------------------------

_DOC = """<!-- leading comment -->

# Current Priorities

- curated baseline

# Availability

- curated availability
"""


def test_insert_then_replace_is_idempotent():
    after_insert = ps.apply_block(_DOC, "Current Priorities", "imessage", ["- derived one"])
    assert "- curated baseline" in after_insert  # curated kept
    assert "- derived one" in after_insert
    assert ps.read_block(after_insert, "imessage") == "- derived one"

    after_replace = ps.apply_block(after_insert, "Current Priorities", "imessage", ["- derived two"])
    assert after_replace.count("BEGIN:ingest:imessage") == 1  # no duplicate fence
    assert "- derived two" in after_replace
    assert "- derived one" not in after_replace
    assert "- curated baseline" in after_replace


def test_distinct_ids_coexist_across_sections():
    # Regression: two blocks with DIFFERENT ids in DIFFERENT sections must both
    # persist (a shared id made the second write clobber the first).
    t = ps.apply_block(_DOC, "Current Priorities", "imessage_priorities", ["- p1"])
    t = ps.apply_block(t, "Availability", "imessage_people", ["- c1"])
    assert ps.read_block(t, "imessage_priorities") == "- p1"
    assert ps.read_block(t, "imessage_people") == "- c1"
    assert t.count("BEGIN:ingest:") == 2


def test_missing_header_raises():
    try:
        ps.apply_block(_DOC, "Nonexistent Section", "x", ["- y"])
    except ValueError:
        return
    raise AssertionError("expected ValueError for missing header")


def test_update_file_dry_run_does_not_write(tmp_path: Path):
    f = tmp_path / "persona.md"
    f.write_text(_DOC, encoding="utf-8")
    changed, new = ps.update_file("Availability", "calendar", ["- free 4pm"], path=f, dry_run=True)
    assert changed is True
    assert "- free 4pm" in new
    assert f.read_text(encoding="utf-8") == _DOC  # unchanged on disk


# --- local_llm strict-local fallback -------------------------------------


def test_summarizer_falls_back_to_deterministic(monkeypatch):
    monkeypatch.setattr(local_llm, "OLLAMA_HOSTS", [])  # no model reachable
    res = local_llm.summarize_to_bullets(
        "facts here",
        "phrase these",
        ["- fallback bullet"],
        strict_local=True,
    )
    assert res.engine == "deterministic"
    assert res.lines == ["- fallback bullet"]


def test_strict_local_never_calls_openai(monkeypatch):
    monkeypatch.setattr(local_llm, "OLLAMA_HOSTS", [])
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-be-used")
    called = {"openai": False}
    monkeypatch.setattr(local_llm, "_try_openai", lambda *_a, **_k: called.__setitem__("openai", True) or "x")
    res = local_llm.summarize_to_bullets("f", "i", ["- fb"], strict_local=True)
    assert called["openai"] is False
    assert res.engine == "deterministic"


def test_as_bullets_normalizes():
    out = local_llm._as_bullets("1. first\n* second\n- third", max_items=5)
    assert out == ["- first", "- second", "- third"]


# --- iMessage parser (fixture DB) ----------------------------------------


def _make_chat_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, text TEXT, handle_id INTEGER,
            date INTEGER, is_from_me INTEGER
        );
        """
    )
    recent_ns = int((time.time() - 3600 - imessage._APPLE_EPOCH_OFFSET) * 1_000_000_000)
    conn.execute("INSERT INTO handle (ROWID, id) VALUES (1, '+15551234821')")
    conn.execute("INSERT INTO handle (ROWID, id) VALUES (2, 'boss@example.com')")
    rows = [
        ("can you send the contract deadline today", 1, 0),
        ("also dinner friday?", 1, 0),
        ("contract review please", 2, 0),
        ("ok thanks", 1, 1),  # from me — excluded from inbound stats
    ]
    for text, hid, mine in rows:
        conn.execute(
            "INSERT INTO message (text, handle_id, date, is_from_me) VALUES (?,?,?,?)",
            (text, hid, recent_ns, mine),
        )
    conn.commit()
    conn.close()


def test_imessage_read_and_derive(tmp_path: Path):
    db = tmp_path / "chat.db"
    _make_chat_db(db)
    rows = imessage.read_recent(db, days=30)
    assert len(rows) == 4
    inbound = [r for r in rows if not r["is_from_me"]]
    assert len(inbound) == 3

    facts, fallback = imessage._contact_facts(rows, {}, top=5)
    # +1555…4821 sent 2 inbound msgs -> ranks first, masked to last 4.
    assert "contact …4821" in facts[0]
    joined = " ".join(facts)
    # Email contact is present but its name/local-part is masked.
    assert "contact …" in joined
    assert "boss" not in joined and "@" not in joined

    topics, _ = imessage._topic_facts(rows, top=8)
    assert "contract" in topics[0]


def test_imessage_skips_when_db_missing(tmp_path: Path):
    rep = imessage.ingest(db_path=tmp_path / "nope.db", dry_run=True)
    assert rep["status"] == "skipped"
    assert "not found" in rep["reason"]


def test_display_handle_masks_pii():
    # All inputs here are synthetic (555 = reserved fake range; example.com).
    assert imessage._display_handle("+15551234821", {}) == "contact …4821"
    # Email: local part (often a name) must NOT appear; stable masked token.
    sample_email = "first.last@example.com"
    label = imessage._display_handle(sample_email, {})
    assert label.startswith("contact …")
    assert "first" not in label.lower() and "@" not in label
    # Stable across calls.
    assert label == imessage._display_handle(sample_email, {})
    # Aliases still win (deliberate, owner-chosen names are fine).
    assert imessage._display_handle("+15551234821", {"+15551234821": "Sam"}) == "Sam"


# --- agent context parser (fixture jsonl) --------------------------------


def test_agent_context_parses_owner_prompts(tmp_path: Path):
    proj = tmp_path / "proj"
    proj.mkdir()
    f = proj / "session.jsonl"
    lines = [
        {"type": "mode", "mode": "normal"},  # ignored
        {"type": "user", "message": {"role": "user", "content": "delegate this to a subagent in parallel"}},
        {"type": "user", "message": {"role": "user", "content": "use haiku, be concise"}},
        {"type": "user", "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},  # filtered
        {"type": "assistant", "message": {"role": "assistant", "content": "ok"}},  # ignored
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},  # not text
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    prompts = agent_context.read_prompts(root=tmp_path)
    assert len(prompts) == 2  # command + tool_result excluded
    facts, fallback_lines = agent_context._derive_facts(prompts)
    assert "delegate" in facts and "haiku" in facts
    assert fallback_lines  # non-empty


def test_agent_context_skips_when_root_missing(tmp_path: Path):
    rep = agent_context.ingest(root=tmp_path / "absent", dry_run=True)
    assert rep["status"] == "skipped"


# --- calendar graceful skip ----------------------------------------------


def test_calendar_skips_without_credentials(tmp_path: Path, monkeypatch):
    # Point credential/token paths at empty temp locations -> must skip cleanly.
    monkeypatch.setattr(gcal, "CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr(gcal, "TOKEN_PATH", tmp_path / "token.json")
    rep = gcal.ingest(dry_run=True)
    assert rep["status"] == "skipped"
    assert "OAuth client" in rep["reason"] or "not installed" in rep["reason"]
