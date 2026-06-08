"""Hermetic tests for tenant-isolated onboarding records."""

from __future__ import annotations

import json

import pytest

import tenant_store


@pytest.fixture(autouse=True)
def local_store(tmp_path, monkeypatch):
    monkeypatch.setenv("TENANT_STORE_LOCAL_PATH", str(tmp_path / "tenants.json"))
    monkeypatch.setenv("TENANT_STORE_FORCE_LOCAL", "true")
    monkeypatch.delenv("TENANT_STORE_REQUIRE_CLOUD", raising=False)
    monkeypatch.setattr(tenant_store, "_backend", tenant_store._Backend())
    yield
    monkeypatch.setattr(tenant_store, "_backend", None)


def test_agent_session_is_isolated_and_token_is_hashed(tmp_path, monkeypatch):
    path = tmp_path / "tenants.json"
    monkeypatch.setenv("TENANT_STORE_LOCAL_PATH", str(path))
    monkeypatch.setattr(tenant_store, "_backend", tenant_store._Backend())

    agent, token = tenant_store.create_agent_session(
        display_name="Ada Owner",
        owner_phone="(650) 555-0100",
        owner_email="ada@example.com",
    )

    stored = json.loads(path.read_text())[agent["record_id"]]
    assert agent["tenant_id"]
    assert token not in path.read_text()
    assert stored["activation_token_sha256"] != token
    assert "activation_token_sha256" not in agent
    assert "voice_clone_id" not in agent
    assert stored["owner_phone"] == "+6505550100"


def test_wrong_token_cannot_read_or_update_agent():
    agent, token = tenant_store.create_agent_session(display_name="Ada")

    assert tenant_store.get_agent(agent["tenant_id"], token)["display_name"] == "Ada"
    with pytest.raises(tenant_store.TenantAuthError):
        tenant_store.get_agent(agent["tenant_id"], "wrong")
    with pytest.raises(tenant_store.TenantAuthError):
        tenant_store.update_agent(agent["tenant_id"], "wrong", {"display_name": "Mallory"})


def test_agent_lookup_by_assigned_phone_and_persona_context():
    agent, token = tenant_store.create_agent_session(display_name="Ada")
    tenant_store.update_agent(
        agent["tenant_id"],
        token,
        {
            "voice_clone_id": "voice-private",
            "voice_clone_consent": True,
            "voice_clone_status": "ready",
            "diction_profile_lines": ["- Match phrases naturally and sparingly: gotchu."],
        },
    )
    assigned = tenant_store.assign_agent_phone(agent["tenant_id"], "+1 (650) 555-0101")
    runtime = tenant_store.get_agent_by_phone("+16505550101")

    assert assigned["activation_status"] == "ready"
    assert runtime is not None
    assert runtime["voice_clone_id"] == "voice-private"
    assert "Ada" in tenant_store.build_persona_context(runtime)
    assert "gotchu" in tenant_store.build_persona_context(runtime)


def test_complete_tenant_activation_is_runtime_ready():
    agent, token = tenant_store.create_agent_session(
        display_name="Ada Owner",
        owner_phone="+16505550100",
        owner_email="ada@example.com",
    )
    tenant_store.update_agent(
        agent["tenant_id"],
        token,
        {
            "voice_clone_id": "voice-private",
            "voice_clone_consent": True,
            "voice_clone_name": "Ada voice",
            "voice_clone_status": "ready",
            "diction_profile_phrases": ["gotchu"],
            "diction_profile_style_notes": ["Prefers short, warm callbacks."],
            "diction_profile_lines": [
                "- Match phrases naturally and sparingly: gotchu.",
                "- Style note: Prefers short, warm callbacks.",
            ],
        },
    )

    public = tenant_store.assign_agent_phone(agent["tenant_id"], "+16505550101")
    runtime = tenant_store.get_agent_by_phone("+16505550101")
    context = tenant_store.build_persona_context(runtime or {})

    assert public["activation_status"] == "ready"
    assert public["agent_phone_source"] == "manual"
    assert public["agent_phone_assigned_at"]
    assert "voice_clone_id" not in public
    assert runtime is not None
    assert runtime["tenant_id"] == agent["tenant_id"]
    assert runtime["voice_clone_id"] == "voice-private"
    assert runtime["voice_clone_consent"] is True
    assert "Ada Owner" in context
    assert "gotchu" in context
    assert "short, warm callbacks" in context


def test_update_agent_ignores_raw_samples_and_unknown_fields():
    agent, token = tenant_store.create_agent_session()
    tenant_store.update_agent(
        agent["tenant_id"],
        token,
        {
            "raw_voice_audio": "never",
            "diction_samples": ["never persist me"],
            "diction_profile_lines": ["safe derived note"],
        },
    )
    stored = tenant_store.authenticate(agent["tenant_id"], token)

    assert "raw_voice_audio" not in stored
    assert "diction_samples" not in stored
    assert stored["diction_profile_lines"] == ["safe derived note"]


def test_update_agent_cannot_forge_google_connection_metadata():
    agent, token = tenant_store.create_agent_session()

    public = tenant_store.update_agent(
        agent["tenant_id"],
        token,
        {
            "gmail_connected_email": "mallory@example.com",
            "gmail_connected_at": "never",
            "calendar_connected_email": "mallory@example.com",
            "calendar_connected_at": "never",
        },
    )
    stored = tenant_store.authenticate(agent["tenant_id"], token)

    assert public["gmail_connected_email"] == ""
    assert public["calendar_connected_email"] == ""
    assert stored["gmail_connected_email"] == ""
    assert stored["calendar_connected_email"] == ""


def test_agent_phone_cannot_be_assigned_to_two_tenants():
    first, _ = tenant_store.create_agent_session()
    second, _ = tenant_store.create_agent_session()
    tenant_store.assign_agent_phone(first["tenant_id"], "+16505550101")

    with pytest.raises(tenant_store.TenantConflict):
        tenant_store.assign_agent_phone(second["tenant_id"], "+16505550101")


def test_count_agent_phone_assignments_by_source_and_time():
    first, _ = tenant_store.create_agent_session()
    second, _ = tenant_store.create_agent_session()
    tenant_store.assign_agent_phone(first["tenant_id"], "+16505550101", source="purchase")
    tenant_store.assign_agent_phone(second["tenant_id"], "+16505550102", source="pool")

    assert tenant_store.count_agent_phone_assignments(source="purchase") == 1
    assert tenant_store.count_agent_phone_assignments(source="pool") == 1
    assert tenant_store.count_agent_phone_assignments() == 2
    assert (
        tenant_store.count_agent_phone_assignments(
            source="purchase",
            since_iso="2999-01-01T00:00:00+00:00",
        )
        == 0
    )


def test_shared_forwarding_routes_by_owner_phone():
    agent, token = tenant_store.create_agent_session(
        display_name="Ada Owner",
        owner_phone="+16505550100",
        owner_email="ada@example.com",
    )
    tenant_store.update_agent(
        agent["tenant_id"],
        token,
        {
            "voice_clone_status": "ready",
            "diction_profile_lines": ["- Says gotchu."],
        },
    )

    public = tenant_store.assign_shared_forwarding_phone(agent["tenant_id"], "+16506444731")
    runtime = tenant_store.get_agent_by_owner_phone("+1 (650) 555-0100")

    assert public["forwarding_phone"] == "+16506444731"
    assert public["routing_mode"] == "shared_forwarded_from"
    assert public["activation_status"] == "ready"
    assert runtime is not None
    assert runtime["tenant_id"] == agent["tenant_id"]


def test_shared_forwarding_rejects_duplicate_owner_phone():
    first, _ = tenant_store.create_agent_session(owner_phone="+16505550100")
    second, _ = tenant_store.create_agent_session(owner_phone="+16505550100")
    tenant_store.assign_shared_forwarding_phone(first["tenant_id"], "+16506444731")

    with pytest.raises(tenant_store.TenantConflict):
        tenant_store.assign_shared_forwarding_phone(second["tenant_id"], "+16506444731")


def test_dynamodb_get_falls_back_to_scan_when_getitem_is_denied():
    class FakeTable:
        def get_item(self, **_kwargs):
            raise Exception("AccessDeniedException: dynamodb:GetItem denied")

        def scan(self, **_kwargs):
            return {
                "Items": [
                    {
                        "record_id": "tenant-agent:test",
                        "type": tenant_store.TYPE_TENANT_AGENT,
                        "tenant_id": "test",
                    }
                ]
            }

    backend = tenant_store._Backend.__new__(tenant_store._Backend)
    backend.kind = "dynamodb"
    backend._ddb_table = FakeTable()

    record = backend.get("tenant-agent:test")

    assert record is not None
    assert record["tenant_id"] == "test"
