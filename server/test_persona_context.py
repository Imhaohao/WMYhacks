"""Tests for the persona context loader (Person 2, Part A).

Run: uv run pytest test_persona_context.py
"""

from __future__ import annotations

from pathlib import Path

import persona_context
import tenant_runtime
from persona_context import (
    PRIVACY_GUARD,
    build_call_persona_context,
    clean_persona_context,
    has_persona_context,
    load_persona_context,
    upload_persona_context,
)


def test_real_file_loads_with_guard(monkeypatch):
    monkeypatch.setattr("persona_context.persona_cloud.fetch_persona", lambda: None)
    ctx = load_persona_context(prefer_cloud=False)
    assert ctx  # the curated baseline exists and is non-empty
    assert "Persona" in ctx
    assert PRIVACY_GUARD.strip()[:20] in ctx
    # HTML comments must be stripped (privacy: don't leak authoring notes).
    assert "<!--" not in ctx
    assert has_persona_context(prefer_cloud=False)


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


def test_cloud_persona_wins_over_local_file(tmp_path: Path, monkeypatch):
    f = tmp_path / "persona.md"
    f.write_text("local context", encoding="utf-8")
    monkeypatch.setattr("persona_context.persona_cloud.fetch_persona", lambda: "cloud context")

    assert load_persona_context(f, with_guard=False, prefer_cloud=True) == "cloud context"


def test_cloud_required_disables_local_fallback(tmp_path: Path, monkeypatch):
    f = tmp_path / "persona.md"
    f.write_text("local context", encoding="utf-8")
    monkeypatch.setattr("persona_context.persona_cloud.fetch_persona", lambda: None)
    monkeypatch.setattr("persona_context.persona_cloud.cloud_required", lambda: True)

    assert load_persona_context(f, prefer_cloud=True) == ""


def test_upload_publishes_cleaned_context(tmp_path: Path, monkeypatch):
    f = tmp_path / "persona.md"
    f.write_text("<!-- local note -->\n# Persona\n\n- concise", encoding="utf-8")
    uploaded = []
    monkeypatch.setattr(
        "persona_context.persona_cloud.publish_persona",
        lambda content: uploaded.append(content) or "dynamodb:test/persona",
    )

    assert upload_persona_context(f) == "dynamodb:test/persona"
    assert uploaded == ["# Persona\n\n- concise"]
    assert clean_persona_context("<!-- hidden -->\nvisible") == "visible"


def test_configured_owner_runtime_normalizes_phone_format(monkeypatch):
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "+1 (650) 555-0100")

    assert tenant_runtime.is_configured_owner_runtime({"owner_phone": "6505550100"})
    assert not tenant_runtime.is_configured_owner_runtime({"owner_phone": "+1 (415) 555-0100"})
    assert not tenant_runtime.is_configured_owner_runtime({"owner_phone": ""})


def test_configured_owner_tenant_gets_tenant_and_global_context(monkeypatch):
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "+16505550100")
    monkeypatch.setattr(
        persona_context.tenant_store,
        "build_persona_context",
        lambda _runtime: "TENANT CONTEXT WITH CALLBACK PRIVACY",
    )
    monkeypatch.setattr(
        "owner_config.build_persona_context",
        lambda: "OWNER CONFIG CONTEXT",
    )
    monkeypatch.setattr(persona_context, "load_persona_context", lambda: "LIVE RICH CONTEXT")

    context = build_call_persona_context({"owner_phone": "6505550100"})

    assert "TENANT CONTEXT WITH CALLBACK PRIVACY" in context
    assert "OWNER CONFIG CONTEXT" in context
    assert "LIVE RICH CONTEXT" in context
    assert context.count("OWNER CONFIG CONTEXT") == 1
    assert context.count("LIVE RICH CONTEXT") == 1


def test_foreign_or_incomplete_tenant_never_reads_global_context(monkeypatch):
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "+16505550100")
    calls: list[str] = []
    monkeypatch.setattr(
        persona_context.tenant_store,
        "build_persona_context",
        lambda _runtime: "TENANT ONLY CONTEXT",
    )
    monkeypatch.setattr(
        "owner_config.build_persona_context",
        lambda: calls.append("owner") or "GLOBAL OWNER CONTEXT",
    )
    monkeypatch.setattr(
        persona_context,
        "load_persona_context",
        lambda: calls.append("live") or "GLOBAL LIVE CONTEXT",
    )

    foreign = build_call_persona_context({"owner_phone": "+14155550100"})
    incomplete = build_call_persona_context({"owner_phone": ""})

    assert foreign == "TENANT ONLY CONTEXT"
    assert incomplete == "TENANT ONLY CONTEXT"
    assert calls == []


def test_legacy_context_reads_owner_and_live_context_once(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "owner_config.build_persona_context",
        lambda: calls.append("owner") or "OWNER CONFIG CONTEXT",
    )
    monkeypatch.setattr(
        persona_context,
        "load_persona_context",
        lambda: calls.append("live") or "LIVE RICH CONTEXT",
    )

    context = build_call_persona_context()

    assert context == "OWNER CONFIG CONTEXT\n\nLIVE RICH CONTEXT"
    assert calls == ["owner", "live"]


def test_optional_owner_context_can_be_absent(monkeypatch):
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "+16505550100")
    monkeypatch.setattr(
        persona_context.tenant_store,
        "build_persona_context",
        lambda _runtime: "TENANT CONTEXT",
    )
    monkeypatch.setattr("owner_config.build_persona_context", lambda: "")
    monkeypatch.setattr(persona_context, "load_persona_context", lambda: "")

    assert build_call_persona_context({"owner_phone": "+16505550100"}) == "TENANT CONTEXT"
