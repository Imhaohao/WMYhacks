"""Hermetic tests for tenant-scoped Google OAuth storage."""

from __future__ import annotations

import json

import tenant_google
import tenant_store


def _reset_vault() -> None:
    tenant_google._vault = None  # type: ignore[attr-defined]


def test_google_token_json_is_separate_from_public_tenant_record(tmp_path, monkeypatch):
    tenant_path = tmp_path / "tenants.json"
    token_path = tmp_path / "google_tokens.json"
    monkeypatch.setenv("TENANT_STORE_LOCAL_PATH", str(tenant_path))
    monkeypatch.setenv("TENANT_STORE_FORCE_LOCAL", "true")
    monkeypatch.setenv("TENANT_GOOGLE_TOKEN_FORCE_LOCAL", "true")
    monkeypatch.setenv("TENANT_GOOGLE_TOKEN_LOCAL_PATH", str(token_path))
    monkeypatch.setattr(tenant_store, "_backend", tenant_store._Backend())
    _reset_vault()

    agent, token = tenant_store.create_agent_session(owner_email="ada@example.com")
    token_json = json.dumps({"refresh_token": "secret-refresh", "client_id": "client-id"})

    tenant_google.save_token_json(
        agent["tenant_id"],
        "gmail",
        token_json,
        email="ada@example.com",
    )
    public = tenant_store.mark_google_connected(
        agent["tenant_id"],
        "gmail",
        "ada@example.com",
    )
    reloaded = tenant_store.get_agent(agent["tenant_id"], token)

    assert public["gmail_connected_email"] == "ada@example.com"
    assert reloaded["gmail_connected_email"] == "ada@example.com"
    assert "refresh_token" not in json.dumps(reloaded)
    assert "secret-refresh" not in tenant_path.read_text()
    assert "secret-refresh" in token_path.read_text()


def test_oauth_state_is_consumed_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_GOOGLE_TOKEN_FORCE_LOCAL", "true")
    monkeypatch.setenv("TENANT_GOOGLE_TOKEN_LOCAL_PATH", str(tmp_path / "google_tokens.json"))
    _reset_vault()

    tenant_google.save_oauth_state(
        "state-1",
        {"tenant_id": "tenant-one", "service": "gmail", "code_verifier": "verifier"},
    )

    assert tenant_google.pop_oauth_state("state-1")["tenant_id"] == "tenant-one"
    assert tenant_google.pop_oauth_state("state-1") is None


def test_status_and_disconnect_are_tenant_scoped(tmp_path, monkeypatch):
    token_path = tmp_path / "google_tokens.json"
    monkeypatch.setenv("TENANT_GOOGLE_TOKEN_FORCE_LOCAL", "true")
    monkeypatch.setenv("TENANT_GOOGLE_TOKEN_LOCAL_PATH", str(token_path))
    _reset_vault()

    tenant_google.save_token_json(
        "tenant-one",
        "calendar",
        json.dumps({"refresh_token": "calendar-refresh"}),
        email="ada@example.com",
    )

    status = tenant_google.status("tenant-one", "calendar")
    assert status["connected"] is True
    assert status["email"] == "ada@example.com"
    assert tenant_google.status("tenant-two", "calendar")["connected"] is False

    assert tenant_google.disconnect("tenant-one", "calendar") is True
    assert tenant_google.status("tenant-one", "calendar")["connected"] is False
