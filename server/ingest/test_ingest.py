"""Hermetic tests for Part B ingestion (no network, no real user data).

Run from server/:  uv run pytest ingest/test_ingest.py -q
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import google_gmail
import persona_context

# Allow `from ingest ...` when pytest is run from server/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import (  # noqa: E402
    agent_context,
    chatgpt_context,
    codex_context,
    discord,
    gcal,
    git_context,
    gmail_context,
    imessage,
    local_llm,
    refresh,
)
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


def test_update_optional_file_removes_stale_generated_block(tmp_path: Path):
    f = tmp_path / "persona.md"
    f.write_text(ps.apply_block(_DOC, "Current Priorities", "lingo", ["- stale"]), encoding="utf-8")
    changed, new = ps.update_optional_file("Current Priorities", "lingo", [], path=f)
    assert changed is True
    assert "BEGIN:ingest:lingo" not in new
    assert "- stale" not in new
    assert "- curated baseline" in new


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


# --- Codex context parser (fixture DB) -----------------------------------


def _make_codex_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE threads (
            first_user_message TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO threads (first_user_message, updated_at) VALUES (?, ?)",
        [
            ("delegate the implementation in parallel and verify tests", 2),
            ("review this concise plan", 1),
            ("", 3),
        ],
    )
    conn.commit()
    conn.close()


def test_codex_context_parses_owner_prompts_without_raw_leak(tmp_path: Path):
    db = tmp_path / "state.sqlite"
    _make_codex_db(db)
    prompts = codex_context.read_prompts(db)
    assert len(prompts) == 2
    facts, fallback = codex_context.memo_context.derive_prompt_facts(prompts)
    assert "delegate" in facts and "verify" in facts
    assert "implementation" not in facts
    assert "implementation" not in " ".join(fallback)


def test_codex_context_skips_when_db_missing(tmp_path: Path):
    rep = codex_context.ingest(db_path=tmp_path / "absent.sqlite", dry_run=True)
    assert rep["status"] == "skipped"


# --- ChatGPT context parser (fixture JSON) -------------------------------


def test_chatgpt_context_extracts_only_user_prompts(tmp_path: Path):
    f = tmp_path / "conversation.json"
    f.write_text(
        json.dumps(
            {
                "mapping": {
                    "1": {
                        "message": {
                            "author": {"role": "user"},
                            "content": {"parts": ["delegate the private-sentinel build and verify tests"]},
                        }
                    },
                    "2": {
                        "message": {
                            "author": {"role": "assistant"},
                            "content": {"parts": ["assistant-only-sentinel"]},
                        }
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    prompts, decoded = chatgpt_context.read_prompts(tmp_path)
    assert decoded == 1
    assert prompts == ["delegate the private-sentinel build and verify tests"]
    facts, fallback = chatgpt_context.memo_context.derive_prompt_facts(prompts)
    rendered = facts + " ".join(fallback)
    assert "delegate" in rendered and "verify" in rendered
    assert "private-sentinel" not in rendered
    assert "assistant-only-sentinel" not in rendered


def test_chatgpt_context_skips_missing_or_binary_store(tmp_path: Path):
    missing = chatgpt_context.ingest(root=tmp_path / "absent", dry_run=True)
    assert missing["status"] == "skipped"

    binary = tmp_path / "conversation.data"
    binary.write_bytes(b"\xff\x00\xfeopaque")
    rep = chatgpt_context.ingest(root=tmp_path, dry_run=True)
    assert rep["status"] == "skipped"
    assert "unsupported binary format" in rep["reason"]


# --- local Git context parser --------------------------------------------


def test_git_context_derives_categories_without_raw_messages():
    facts, fallback = git_context._derive_facts(
        {
            "commits": ["fix secret-project-name latency", "Merge branch 'secret-work'"],
            "reflog": ["commit: fix secret-project-name latency", "pull: Fast-forward"],
            "branches": ["main", "secret-work"],
        }
    )
    rendered = facts + " ".join(fallback)
    assert "fix=1" in rendered and "merge=1" in rendered
    assert "commit=1" in rendered and "pull=1" in rendered
    assert "secret" not in rendered


def test_git_context_skips_when_repo_missing(tmp_path: Path):
    rep = git_context.ingest(repo=tmp_path / "absent", dry_run=True)
    assert rep["status"] == "skipped"


def test_refresh_accepts_new_memo_sources(monkeypatch):
    seen = []

    def fake(name, days, dry_run, discord_path):
        seen.append(name)
        return {"source": name, "status": "ok", "blocks": []}

    monkeypatch.setattr(refresh, "_run_source", fake)
    assert refresh.main(["--sources", "gmail,agent,codex,chatgpt,git", "--dry-run"]) == 0
    assert seen == ["gmail", "agent", "codex", "chatgpt", "git"]


def test_refresh_uploads_cleaned_cloud_persona(monkeypatch):
    monkeypatch.setattr(
        refresh,
        "_run_source",
        lambda name, days, dry_run, discord_path: {
            "source": name,
            "status": "ok",
            "blocks": [],
        },
    )
    uploaded = []
    monkeypatch.setattr(
        persona_context,
        "upload_persona_context",
        lambda: uploaded.append(True) or "dynamodb:test/persona",
    )

    assert refresh.main(["--sources", "git"]) == 0
    assert uploaded == [True]


# --- Gmail sent-mail style -----------------------------------------------


def test_gmail_style_facts_never_include_raw_email_text():
    secret = "private-sentinel-project"
    facts, fallback = gmail_context._style_facts(
        [
            f"Hi team,\n\nQuick update on {secret}. Can you review?\n\nThanks,\nMe",
            "Sharing the status now.\n\nBest,\nMe",
        ]
    )
    rendered = "\n".join(facts + fallback)
    assert "sent emails sampled: 2" in rendered
    assert secret not in rendered
    assert fallback


def test_gmail_strips_quoted_reply():
    body = "My fresh answer.\n\nOn Sat, May 30, 2026 wrote:\n> private quoted history"
    assert google_gmail._strip_quoted_reply(body) == "My fresh answer."


def test_gmail_context_skips_without_connection(monkeypatch):
    monkeypatch.setattr(gmail_context.google_gmail, "is_connected", lambda: False)
    rep = gmail_context.ingest(dry_run=True)
    assert rep["status"] == "skipped"
    assert "reconnect Gmail" in rep["reason"]


# --- Discord export parser (DiscordChatExporter + official) --------------


def _write_dce_export(path: Path) -> None:
    """A DiscordChatExporter-shape JSON: owner 'me' + one inbound contact."""
    now = time.gmtime()
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", now)
    obj = {
        "channel": {"name": "dms"},
        "messages": [
            {"timestamp": ts, "content": "lol yeah deploying the bot rn fr",
             "author": {"id": "1", "name": "me"}},
            {"timestamp": ts, "content": "ngl the latency looks good",
             "author": {"id": "1", "name": "me"}},
            {"timestamp": ts, "content": "can you review the deploy pipeline",
             "author": {"id": "2", "name": "teammate"}},
            {"timestamp": ts, "content": "",  # attachment-only -> dropped
             "author": {"id": "2", "name": "teammate"}},
        ],
    }
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_discord_dce_separates_owner_voice(tmp_path: Path):
    f = tmp_path / "discord_export.json"
    _write_dce_export(f)
    msgs = discord.load_export(f, owner="1")
    assert len(msgs) == 3  # empty-content message dropped
    outbound = [m for m in msgs if m.is_from_me]
    assert len(outbound) == 2  # both 'me' messages
    assert all(not m.is_from_me for m in msgs if m.author_id == "2")

    facts, fallback = discord._style_facts(outbound)
    assert any("lol" in line or "fr" in line or "ngl" in line for line in facts)
    assert fallback  # non-empty deterministic voice bullets


def test_discord_owner_match_by_username(tmp_path: Path):
    f = tmp_path / "discord_export.json"
    _write_dce_export(f)
    # Owner given as a username (case-insensitive) instead of id.
    msgs = discord.load_export(f, owner="ME")
    assert sum(m.is_from_me for m in msgs) == 2


def test_discord_deidentifies_contacts():
    # Username never appears; stable hashed token; alias wins when provided.
    label = discord._display_author("2", "teammate", {})
    assert label.startswith("user …")
    assert "teammate" not in label
    assert label == discord._display_author("2", "teammate", {})  # stable
    assert discord._display_author("2", "teammate", {"2": "Dana"}) == "Dana"
    assert discord._display_author("2", "teammate", {"teammate": "Dana"}) == "Dana"


def test_discord_official_csv_is_all_outbound(tmp_path: Path):
    f = tmp_path / "messages.csv"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    f.write_text(
        "ID,Timestamp,Contents,Attachments\n"
        f"1,{ts},shipping the eval loop today,\n"
        f"2,{ts},,\n",  # empty contents -> dropped
        encoding="utf-8",
    )
    msgs = discord.load_export(f)
    assert len(msgs) == 1
    assert msgs[0].is_from_me is True  # official export is owner-only


def test_discord_skips_when_export_missing(tmp_path: Path):
    rep = discord.ingest(export_path=tmp_path / "nope.json", dry_run=True)
    assert rep["status"] == "skipped"
    assert "no export" in rep["reason"]


def test_discord_no_owner_means_no_outbound(tmp_path: Path):
    # Without an owner, nothing is flagged outbound -> the Persona voice block is
    # skipped (ingest emits a 'voice_skipped' note) while topics/people still derive.
    f = tmp_path / "discord_export.json"
    _write_dce_export(f)
    msgs = discord.load_export(f, owner=None)
    assert msgs and not any(m.is_from_me for m in msgs)
    assert discord._style_facts([]) == ([], [])  # no outbound -> empty voice facts


# --- calendar graceful skip ----------------------------------------------


def test_calendar_skips_without_credentials(tmp_path: Path, monkeypatch):
    # Point credential/token paths at empty temp locations -> must skip cleanly.
    monkeypatch.setattr(gcal, "CREDENTIALS_PATH", tmp_path / "credentials.json")
    monkeypatch.setattr(gcal, "TOKEN_PATH", tmp_path / "token.json")
    rep = gcal.ingest(dry_run=True)
    assert rep["status"] == "skipped"
    assert "OAuth client" in rep["reason"] or "not installed" in rep["reason"]
