"""Gate C — message-context digest, persona injection, and privacy invariants."""

from __future__ import annotations

import json

import pytest

import message_digest
import owner_config

# A sentinel standing in for a raw chat body that must never leak into the
# digest, the prompt, or persisted config.
RAW_BODY_SENTINEL = "SECRET_RAW_BODY_must_not_leak"


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point owner_config + digest source at temp files (never touch real ones)."""
    cfg_path = tmp_path / "owner_config.json"
    digest_path = tmp_path / "message_digest.json"
    monkeypatch.setattr(owner_config, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(message_digest, "DIGEST_SOURCE_PATH", digest_path)
    return cfg_path, digest_path


def _write_digest_source(path, *, with_raw_body: bool = True) -> None:
    entry = {"who": "Sarah", "topic": "lease renewal", "urgency": "waiting on reply"}
    if with_raw_body:
        entry["raw_body"] = RAW_BODY_SENTINEL  # producer must ignore this
    path.write_text(json.dumps({"entries": [entry]}), encoding="utf-8")


def test_persona_context_includes_manual_summary(isolated_store):
    owner_config.save_owner_config(
        {"display_name": "Alex", "message_context_summary": "Mom is in town this week"}
    )
    ctx = owner_config.build_persona_context()
    assert "Recent context (owner-provided summary): Mom is in town this week" in ctx


def test_persona_context_labels_synced_summary(isolated_store):
    owner_config.save_owner_config(
        {"message_context_summary": "lease renewal pending", "message_context_source": "imessage"}
    )
    ctx = owner_config.build_persona_context()
    assert "Recent message context (auto-synced summary):" in ctx


def test_persona_context_truncates_to_limit(isolated_store):
    owner_config.save_owner_config({"message_context_summary": "x" * 5000})
    ctx = owner_config.build_persona_context()
    assert len(ctx) <= owner_config._PERSONA_CONTEXT_LIMIT
    assert ctx.endswith("…")


def test_sync_writes_redacted_digest(isolated_store):
    _, digest_path = isolated_store
    _write_digest_source(digest_path)
    result = message_digest.sync_message_context(force=True)
    assert "lease renewal" in result["message_context_summary"]
    assert result["message_context_source"] == "imessage"
    assert result["message_context_synced_at"]


def test_explicit_sync_overrides_manual_but_auto_sync_does_not(isolated_store):
    _, digest_path = isolated_store
    _write_digest_source(digest_path)
    owner_config.save_owner_config(
        {"message_context_summary": "owner typed this", "message_context_source": "manual"}
    )

    # Auto-sync (force=False) must not clobber the owner's manual edit.
    auto = message_digest.sync_message_context(force=False)
    assert auto["message_context_summary"] == "owner typed this"
    assert auto["message_context_source"] == "manual"

    # Explicit re-sync (force=True) replaces it.
    explicit = message_digest.sync_message_context(force=True)
    assert "lease renewal" in explicit["message_context_summary"]
    assert explicit["message_context_source"] == "imessage"


def test_raw_message_bodies_never_persist(isolated_store):
    """Privacy invariant: raw bodies reach neither the digest, prompt, nor disk."""
    cfg_path, digest_path = isolated_store
    _write_digest_source(digest_path, with_raw_body=True)

    digest = message_digest.build_message_digest()
    assert RAW_BODY_SENTINEL not in digest
    assert "lease renewal" in digest  # topic survives, body does not

    message_digest.sync_message_context(force=True)
    persisted = cfg_path.read_text(encoding="utf-8")
    assert RAW_BODY_SENTINEL not in persisted

    ctx = owner_config.build_persona_context()
    assert RAW_BODY_SENTINEL not in ctx
