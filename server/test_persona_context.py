"""Tests for the persona context loader (Person 2, Part A).

Run: uv run pytest test_persona_context.py
"""

from __future__ import annotations

from pathlib import Path

from persona_context import (
    PRIVACY_GUARD,
    has_persona_context,
    load_persona_context,
)


def test_real_file_loads_with_guard():
    ctx = load_persona_context()
    assert ctx  # the curated baseline exists and is non-empty
    assert "Persona" in ctx
    assert PRIVACY_GUARD.strip()[:20] in ctx
    # HTML comments must be stripped (privacy: don't leak authoring notes).
    assert "<!--" not in ctx
    assert has_persona_context()


def test_missing_file_returns_empty(tmp_path: Path):
    missing = tmp_path / "does_not_exist.md"
    assert load_persona_context(missing) == ""
    assert has_persona_context(missing) is False


def test_empty_file_returns_empty(tmp_path: Path):
    empty = tmp_path / "empty.md"
    empty.write_text("   \n\t  \n")
    assert load_persona_context(empty) == ""
    assert has_persona_context(empty) is False


def test_comment_only_file_returns_empty(tmp_path: Path):
    f = tmp_path / "comments.md"
    f.write_text("<!-- just a note -->\n<!-- another -->")
    assert load_persona_context(f) == ""


def test_guard_can_be_disabled(tmp_path: Path):
    f = tmp_path / "c.md"
    f.write_text("Hello context")
    assert load_persona_context(f, with_guard=False) == "Hello context"
    assert PRIVACY_GUARD.strip()[:20] in load_persona_context(f, with_guard=True)
