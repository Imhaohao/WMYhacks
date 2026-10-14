"""Hermetic API tests for tenant-isolated cloud onboarding."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

import onboarding_api
import tenant_google
import tenant_store


@pytest.fixture(autouse=True)
def local_tenant_api(tmp_path, monkeypatch):
    path = tmp_path / "tenants.json"
    monkeypatch.setenv("TENANT_STORE_LOCAL_PATH", str(path))
    monkeypatch.setenv("TENANT_STORE_FORCE_LOCAL", "true")
    monkeypatch.setenv("TENANT_GOOGLE_TOKEN_FORCE_LOCAL", "true")
    monkeypatch.setenv("TENANT_GOOGLE_TOKEN_LOCAL_PATH", str(tmp_path / "google_tokens.json"))
    monkeypatch.setenv("ENABLE_PUBLIC_TENANT_ONBOARDING", "true")
    monkeypatch.setenv("ONBOARDING_ADMIN_KEY", "admin-secret")
    monkeypatch.delenv("ENABLE_AUTO_AGENT_PHONE_ASSIGNMENT", raising=False)
    monkeypatch.delenv("TENANT_AGENT_PHONE_POOL", raising=False)
    monkeypatch.delenv("ENABLE_AUTO_AGENT_PHONE_PURCHASE", raising=False)
    monkeypatch.delenv("TWILIO_AUTO_PURCHASE_COUNTRY", raising=False)
    monkeypatch.delenv("TWILIO_AUTO_PURCHASE_AREA_CODE", raising=False)
    monkeypatch.delenv("TWILIO_AUTO_PURCHASE_CONTAINS", raising=False)
    monkeypatch.delenv("TWILIO_AUTO_PURCHASE_NEAR_NUMBER", raising=False)
    monkeypatch.delenv("TWILIO_AUTO_PURCHASE_NEAR_OWNER", raising=False)
    monkeypatch.delenv("TWILIO_AUTO_PURCHASE_DAILY_LIMIT", raising=False)
    monkeypatch.delenv("TWILIO_AUTO_PURCHASE_REQUIRE_CLOUD", raising=False)
    monkeypatch.delenv("ENABLE_SHARED_FORWARDING_ROUTING", raising=False)
    monkeypatch.delenv("SHARED_FORWARDING_PHONE_NUMBER", raising=False)
    monkeypatch.delenv("ENABLE_PUBLIC_VOICE_CLONING", raising=False)
    monkeypatch.setattr(tenant_store, "_backend", tenant_store._Backend())
    monkeypatch.setattr(tenant_google, "_vault", None)
    yield path
    monkeypatch.setattr(tenant_store, "_backend", None)
    monkeypatch.setattr(tenant_google, "_vault", None)


def _new_session() -> tuple[dict, str]:
    created = onboarding_api.create_tenant_session(
        onboarding_api.TenantSessionBody(
            display_name="Ada Owner",
            owner_phone="+16505550100",
            owner_email="ada@example.com",
        )
    )
    return created["agent"], created["onboarding_token"]


def _complete_activation_profile(agent: dict, token: str) -> dict:
    return tenant_store.update_agent(
        agent["tenant_id"],
        token,
        {
            "voice_clone_id": "voice-test",
            "voice_clone_consent": True,
            "voice_clone_name": "Ada voice",
            "voice_clone_status": "ready",
            "diction_profile_phrases": ["gotchu"],
            "diction_profile_style_notes": ["Speaks briefly."],
            "diction_profile_lines": ["- Uses 'gotchu' for quick acknowledgement."],
        },
    )


class _FakeUpload:
    filename = "owner-voice.webm"
    content_type = "audio/webm"

    async def read(self) -> bytes:
        return b"voice-sample" * 3000


def test_public_tenant_onboarding_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_PUBLIC_TENANT_ONBOARDING", "false")

    with pytest.raises(HTTPException) as exc:
        onboarding_api.create_tenant_session(onboarding_api.TenantSessionBody())

    assert exc.value.status_code == 503


def test_session_profile_and_forwarding_are_owner_scoped():
    agent, token = _new_session()
    tenant_id = agent["tenant_id"]

    saved = onboarding_api.save_tenant_profile(
        tenant_id,
        onboarding_api.TenantSessionBody(
            display_name="Ada Updated",
            owner_phone="+16505550100",
            owner_email="ada@example.com",
            timezone="America/New_York",
        ),
        x_onboarding_token=token,
    )
    forwarded = onboarding_api.save_tenant_voicemail_forwarding(
        tenant_id,
        onboarding_api.VoicemailForwardingBody(confirmed=True),
        x_onboarding_token=token,
    )

    assert saved["agent"]["display_name"] == "Ada Updated"
    assert saved["agent"]["timezone"] == "America/New_York"
    assert forwarded["agent"]["voicemail_forwarding_confirmed"] is True
    with pytest.raises(HTTPException) as exc:
        onboarding_api.get_tenant_session(tenant_id, x_onboarding_token="wrong")
    assert exc.value.status_code == 403


def test_diction_save_discards_raw_replies(local_tenant_api):
    agent, token = _new_session()
    samples = [
        "yo gotchu, i'll call after lunch",
        "yo gotchu, i'll circle back soon",
        "yo gotchu, i'll text you later",
    ]
    saved = onboarding_api.save_tenant_diction_profile(
        agent["tenant_id"],
        onboarding_api.DictionProfileBody(samples=samples, phrases=["talk soon"]),
        x_onboarding_token=token,
    )
    raw_store = local_tenant_api.read_text()

    assert saved["profile"]["ready"] is True
    assert "after lunch" not in raw_store
    assert "diction_samples" not in raw_store
    assert "talk soon" in raw_store


def test_phone_assignment_requires_admin_key():
    agent, _ = _new_session()

    with pytest.raises(HTTPException) as exc:
        onboarding_api.assign_tenant_agent_phone(
            agent["tenant_id"],
            onboarding_api.TenantPhoneAssignmentBody(agent_phone="+16505550101"),
            x_provisioning_key="wrong",
        )
    assert exc.value.status_code == 403

    assigned = onboarding_api.assign_tenant_agent_phone(
        agent["tenant_id"],
        onboarding_api.TenantPhoneAssignmentBody(agent_phone="+16505550101"),
        x_provisioning_key="admin-secret",
    )
    assert assigned["agent"]["agent_phone"] == "+16505550101"


def test_phone_assignment_wires_twilio_webhook(monkeypatch):
    agent, _ = _new_session()
    called = {}
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "wire_inbound_webhook",
        lambda phone: called.update(phone=phone)
        or {"ok": True, "voice_url": "https://voice.example.test/"},
    )

    assigned = onboarding_api.assign_tenant_agent_phone(
        agent["tenant_id"],
        onboarding_api.TenantPhoneAssignmentBody(agent_phone="+16505550101"),
        x_provisioning_key="admin-secret",
    )

    assert called == {"phone": "+16505550101"}
    assert assigned["webhook"]["ok"] is True


def test_session_creation_defers_phone_provisioning():
    created = onboarding_api.create_tenant_session(
        onboarding_api.TenantSessionBody(
            display_name="Grace Owner",
            owner_phone="+16505550199",
            owner_email="grace@example.com",
        )
    )

    assert created["agent"]["agent_phone"] == ""
    assert created["phone_assignment"]["mode"] == "deferred"
    assert created["phone_assignment"]["assigned"] is False


def test_activation_needs_only_the_profile_not_voice_or_diction(monkeypatch):
    monkeypatch.setenv("ENABLE_SHARED_FORWARDING_ROUTING", "true")
    monkeypatch.setenv("SHARED_FORWARDING_PHONE_NUMBER", "+16506444731")
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "wire_inbound_webhook",
        lambda phone: {"ok": True, "voice_url": "https://voice.example.test/", "phone": phone},
    )
    agent, token = _new_session()

    activated = onboarding_api.activate_tenant_agent(agent["tenant_id"], x_onboarding_token=token)

    assert activated["agent"]["forwarding_phone"] == "+16506444731"
    assert activated["agent"]["activation_status"] == "ready"


def test_activation_still_requires_owner_phone():
    agent, token = _new_session()
    tenant_store.update_agent(agent["tenant_id"], token, {"owner_phone": ""})

    with pytest.raises(HTTPException) as exc:
        onboarding_api.activate_tenant_agent(agent["tenant_id"], x_onboarding_token=token)

    assert exc.value.status_code == 422
    assert exc.value.detail["missing"] == ["owner_phone"]


def _voicemail_record(tenant_id: str, name: str) -> dict:
    return {
        "record_id": f"rec-{name}",
        "type": "voicemail",
        "owner_context_tag": tenant_id,
        "timestamp": "2026-10-14T21:04:00+00:00",
        "voicemail": {
            "caller_number": "+14155550143",
            "transcript": "Caller: hi it's about the lease",
            "message": {"caller_name": name, "reason": "the lease renewal"},
            "sms_sent": True,
        },
    }


def test_calls_list_only_the_tenants_own_screened_calls(monkeypatch):
    agent, token = _new_session()
    other, _ = _new_session()
    records = [
        _voicemail_record(agent["tenant_id"], "Maya"),
        _voicemail_record(other["tenant_id"], "Intruder"),
    ]
    monkeypatch.setattr(onboarding_api.screened_calls.persistence, "list_records", lambda _t: records)

    result = onboarding_api.list_tenant_calls(agent["tenant_id"], x_onboarding_token=token)

    assert [call["caller_name"] for call in result["calls"]] == ["Maya"]
    assert result["calls"][0]["texted"] is True
    with pytest.raises(HTTPException) as exc:
        onboarding_api.list_tenant_calls(other["tenant_id"], x_onboarding_token=token)
    assert exc.value.status_code == 403


def test_activation_can_auto_assign_from_phone_pool(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTO_AGENT_PHONE_ASSIGNMENT", "true")
    monkeypatch.setenv("TENANT_AGENT_PHONE_POOL", "+16505550101,+16505550102")
    wired = []
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "wire_inbound_webhook",
        lambda phone: wired.append(phone)
        or {"ok": True, "voice_url": "https://voice.example.test/"},
    )

    first, first_token = _new_session()
    second, second_token = _new_session()
    _complete_activation_profile(first, first_token)
    _complete_activation_profile(second, second_token)
    first_activated = onboarding_api.activate_tenant_agent(
        first["tenant_id"], x_onboarding_token=first_token
    )
    second_activated = onboarding_api.activate_tenant_agent(
        second["tenant_id"], x_onboarding_token=second_token
    )

    assert first_activated["agent"]["agent_phone"] == "+16505550101"
    assert second_activated["agent"]["agent_phone"] == "+16505550102"
    assert first_activated["agent"]["agent_phone_source"] == "pool"
    assert second_activated["agent"]["agent_phone_source"] == "pool"
    assert first_activated["agent"]["activation_status"] == "ready"
    assert second_activated["agent"]["activation_status"] == "ready"
    assert wired == ["+16505550101", "+16505550102"]


def test_activation_does_not_assign_pool_number_when_webhook_fails(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTO_AGENT_PHONE_ASSIGNMENT", "true")
    monkeypatch.setenv("TENANT_AGENT_PHONE_POOL", "+16505550101")
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "wire_inbound_webhook",
        lambda phone: {"ok": False, "detail": f"{phone} is not owned by this account."},
    )
    agent, token = _new_session()
    _complete_activation_profile(agent, token)

    activated = onboarding_api.activate_tenant_agent(
        agent["tenant_id"], x_onboarding_token=token
    )

    assert activated["agent"]["agent_phone"] == ""
    assert activated["agent"]["activation_status"] == "draft"
    assert activated["phone_assignment"]["mode"] == "pool"
    assert activated["phone_assignment"]["assigned"] is False
    assert activated["phone_assignment"]["webhook_failures"] == [
        {
            "phone": "+16505550101",
            "detail": "+16505550101 is not owned by this account.",
        }
    ]


def test_activation_continues_when_phone_pool_is_exhausted(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTO_AGENT_PHONE_ASSIGNMENT", "true")
    monkeypatch.setenv("TENANT_AGENT_PHONE_POOL", "+16505550101")
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "wire_inbound_webhook",
        lambda phone: {"ok": True, "voice_url": "https://voice.example.test/"},
    )

    first, first_token = _new_session()
    second, second_token = _new_session()
    _complete_activation_profile(first, first_token)
    _complete_activation_profile(second, second_token)
    first_activated = onboarding_api.activate_tenant_agent(
        first["tenant_id"], x_onboarding_token=first_token
    )
    second_activated = onboarding_api.activate_tenant_agent(
        second["tenant_id"], x_onboarding_token=second_token
    )

    assert first_activated["agent"]["agent_phone"] == "+16505550101"
    assert second_activated["agent"]["agent_phone"] == ""
    assert second_activated["phone_assignment"]["assigned"] is False
    assert "already assigned" in second_activated["phone_assignment"]["detail"]


def test_activation_does_not_purchase_phone_unless_enabled(monkeypatch):
    agent, token = _new_session()
    _complete_activation_profile(agent, token)
    called = {"purchase": False}
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "purchase_and_wire_local_number",
        lambda **_kwargs: called.__setitem__("purchase", True) or {"ok": True},
    )

    activated = onboarding_api.activate_tenant_agent(
        agent["tenant_id"], x_onboarding_token=token
    )

    assert activated["agent"]["agent_phone"] == ""
    assert activated["phone_assignment"] == {"mode": "disabled", "assigned": False}
    assert called == {"purchase": False}


def test_activation_can_purchase_and_assign_phone(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTO_AGENT_PHONE_PURCHASE", "true")
    monkeypatch.setenv("TWILIO_AUTO_PURCHASE_REQUIRE_CLOUD", "false")
    agent, token = _new_session()
    _complete_activation_profile(agent, token)
    called = {}

    def fake_purchase(**kwargs):
        called.update(kwargs)
        return {
            "ok": True,
            "agent_phone": "+16505550177",
            "voice_url": "https://voice.example.test/",
            "detail": "Purchased +16505550177",
            "candidate": {"phone_number": "+16505550177", "locality": "San Mateo"},
        }

    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "purchase_and_wire_local_number",
        fake_purchase,
    )

    activated = onboarding_api.activate_tenant_agent(
        agent["tenant_id"], x_onboarding_token=token
    )

    assert activated["agent"]["agent_phone"] == "+16505550177"
    assert activated["agent"]["activation_status"] == "ready"
    assert activated["agent"]["agent_phone_source"] == "purchase"
    assert activated["agent"]["agent_phone_assigned_at"]
    assert activated["phone_assignment"]["mode"] == "purchase"
    assert activated["phone_assignment"]["assigned"] is True
    assert activated["phone_assignment"]["purchase"]["voice_url"] == "https://voice.example.test/"
    assert called["country"] == "US"
    assert called["area_code"] == "650"
    assert called["near_number"] == "+16505550100"


def test_activation_is_idempotent_for_existing_agent_phone(monkeypatch):
    agent, token = _new_session()
    _complete_activation_profile(agent, token)
    tenant_store.assign_agent_phone(agent["tenant_id"], "+16505550177")
    called = {}
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "wire_inbound_webhook",
        lambda phone: called.update(phone=phone)
        or {"ok": True, "voice_url": "https://voice.example.test/"},
    )

    activated = onboarding_api.activate_tenant_agent(
        agent["tenant_id"], x_onboarding_token=token
    )

    assert activated["agent"]["agent_phone"] == "+16505550177"
    assert activated["phone_assignment"]["mode"] == "existing"
    assert called == {"phone": "+16505550177"}


def test_activation_pool_exhaustion_can_fall_back_to_purchase(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTO_AGENT_PHONE_ASSIGNMENT", "true")
    monkeypatch.setenv("TENANT_AGENT_PHONE_POOL", "+16505550101")
    monkeypatch.setenv("ENABLE_AUTO_AGENT_PHONE_PURCHASE", "true")
    monkeypatch.setenv("TWILIO_AUTO_PURCHASE_REQUIRE_CLOUD", "false")
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "wire_inbound_webhook",
        lambda phone: {"ok": True, "voice_url": "https://voice.example.test/"},
    )
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "purchase_and_wire_local_number",
        lambda **_kwargs: {
            "ok": True,
            "agent_phone": "+16505550102",
            "voice_url": "https://voice.example.test/",
            "detail": "Purchased +16505550102",
        },
    )

    first, first_token = _new_session()
    second, second_token = _new_session()
    _complete_activation_profile(first, first_token)
    _complete_activation_profile(second, second_token)
    first_activated = onboarding_api.activate_tenant_agent(
        first["tenant_id"], x_onboarding_token=first_token
    )
    second_activated = onboarding_api.activate_tenant_agent(
        second["tenant_id"], x_onboarding_token=second_token
    )

    assert first_activated["agent"]["agent_phone"] == "+16505550101"
    assert second_activated["agent"]["agent_phone"] == "+16505550102"
    assert second_activated["phone_assignment"]["mode"] == "purchase"


def test_activation_continues_when_phone_purchase_fails(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTO_AGENT_PHONE_PURCHASE", "true")
    monkeypatch.setenv("TWILIO_AUTO_PURCHASE_REQUIRE_CLOUD", "false")
    agent, token = _new_session()
    _complete_activation_profile(agent, token)
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "purchase_and_wire_local_number",
        lambda **_kwargs: {"ok": False, "detail": "No numbers available."},
    )

    activated = onboarding_api.activate_tenant_agent(
        agent["tenant_id"], x_onboarding_token=token
    )

    assert activated["agent"]["agent_phone"] == ""
    assert activated["phone_assignment"]["mode"] == "purchase"
    assert activated["phone_assignment"]["assigned"] is False
    assert activated["phone_assignment"]["detail"] == "No numbers available."


def test_activation_can_assign_shared_forwarding_number(monkeypatch):
    monkeypatch.setenv("ENABLE_SHARED_FORWARDING_ROUTING", "true")
    monkeypatch.setenv("SHARED_FORWARDING_PHONE_NUMBER", "+16506444731")
    agent, token = _new_session()
    _complete_activation_profile(agent, token)
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "wire_inbound_webhook",
        lambda phone: {"ok": True, "voice_url": "https://voice.example.test/", "phone": phone},
    )

    activated = onboarding_api.activate_tenant_agent(
        agent["tenant_id"], x_onboarding_token=token
    )

    assert activated["agent"]["forwarding_phone"] == "+16506444731"
    assert activated["agent"]["routing_mode"] == "shared_forwarded_from"
    assert activated["agent"]["activation_status"] == "ready"
    assert activated["phone_assignment"]["mode"] == "shared_forwarding"
    assert activated["phone_assignment"]["assigned"] is True
    assert activated["phone_assignment"]["forwarding_phone"] == "+16506444731"


def test_activation_does_not_assign_shared_forwarding_when_webhook_fails(monkeypatch):
    monkeypatch.setenv("ENABLE_SHARED_FORWARDING_ROUTING", "true")
    monkeypatch.setenv("SHARED_FORWARDING_PHONE_NUMBER", "+16506444731")
    agent, token = _new_session()
    _complete_activation_profile(agent, token)
    monkeypatch.setattr(
        onboarding_api.twilio_provisioning,
        "wire_inbound_webhook",
        lambda phone: {
            "ok": False,
            "detail": f"{phone} webhook could not be updated.",
        },
    )

    activated = onboarding_api.activate_tenant_agent(
        agent["tenant_id"], x_onboarding_token=token
    )

    assert activated["agent"]["forwarding_phone"] == ""
    assert activated["agent"]["routing_mode"] in {"", None}
    assert activated["agent"]["activation_status"] == "draft"
    assert activated["phone_assignment"]["mode"] == "shared_forwarding"
    assert activated["phone_assignment"]["assigned"] is False
    assert activated["phone_assignment"]["webhook"]["ok"] is False


def test_activation_purchase_requires_cloud_by_default(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTO_AGENT_PHONE_PURCHASE", "true")
    agent, token = _new_session()
    _complete_activation_profile(agent, token)

    activated = onboarding_api.activate_tenant_agent(
        agent["tenant_id"], x_onboarding_token=token
    )

    assert activated["phone_assignment"]["mode"] == "purchase"
    assert activated["phone_assignment"]["assigned"] is False
    assert "cloud tenant storage" in activated["phone_assignment"]["detail"]


def test_activation_purchase_respects_daily_limit(monkeypatch):
    monkeypatch.setenv("ENABLE_AUTO_AGENT_PHONE_PURCHASE", "true")
    monkeypatch.setenv("TWILIO_AUTO_PURCHASE_REQUIRE_CLOUD", "false")
    monkeypatch.setenv("TWILIO_AUTO_PURCHASE_DAILY_LIMIT", "1")
    first, first_token = _new_session()
    second, second_token = _new_session()
    _complete_activation_profile(first, first_token)
    _complete_activation_profile(second, second_token)
    tenant_store.assign_agent_phone(first["tenant_id"], "+16505550177", source="purchase")

    activated = onboarding_api.activate_tenant_agent(
        second["tenant_id"], x_onboarding_token=second_token
    )

    assert activated["phone_assignment"]["mode"] == "purchase"
    assert activated["phone_assignment"]["assigned"] is False
    assert activated["phone_assignment"]["daily_limit"] == 1
    assert activated["phone_assignment"]["purchases_today"] == 1


def test_phone_assignment_rejects_cross_tenant_collision():
    first, _ = _new_session()
    second, _ = _new_session()
    body = onboarding_api.TenantPhoneAssignmentBody(agent_phone="+16505550101")
    onboarding_api.assign_tenant_agent_phone(
        first["tenant_id"], body, x_provisioning_key="admin-secret"
    )

    with pytest.raises(HTTPException) as exc:
        onboarding_api.assign_tenant_agent_phone(
            second["tenant_id"], body, x_provisioning_key="admin-secret"
        )
    assert exc.value.status_code == 409


def test_voice_cloning_requires_separate_paid_operation_switch():
    with pytest.raises(HTTPException) as exc:
        onboarding_api._require_public_voice_cloning()
    assert exc.value.status_code == 503


def test_tenant_google_auth_url_requires_token(monkeypatch):
    agent, token = _new_session()
    called = {}
    monkeypatch.setattr(tenant_google, "is_configured", lambda service: True)
    monkeypatch.setattr(
        tenant_google,
        "create_auth_url",
        lambda tenant_id, service: called.update(tenant_id=tenant_id, service=service)
        or "https://accounts.google.test/auth",
    )

    with pytest.raises(HTTPException) as exc:
        onboarding_api.tenant_google_auth_url(
            agent["tenant_id"],
            "gmail",
            x_onboarding_token="wrong",
        )
    assert exc.value.status_code == 403
    assert called == {}

    result = onboarding_api.tenant_google_auth_url(
        agent["tenant_id"],
        "gmail",
        x_onboarding_token=token,
    )
    assert result["ok"] is True
    assert result["url"] == "https://accounts.google.test/auth"
    assert called == {"tenant_id": agent["tenant_id"], "service": "gmail"}


def test_tenant_google_callback_marks_connected_without_public_token(monkeypatch, local_tenant_api):
    agent, token = _new_session()
    monkeypatch.setattr(
        tenant_google,
        "exchange_code",
        lambda service, code, state: (agent["tenant_id"], "ada@example.com"),
    )

    response = onboarding_api.tenant_google_callback("calendar", code="code", state="state")
    status = tenant_store.get_agent(agent["tenant_id"], token)
    raw_store = local_tenant_api.read_text()

    assert response.status_code == 200
    assert status["calendar_connected_email"] == "ada@example.com"
    assert "refresh_token" not in json.dumps(status)
    assert "refresh_token" not in raw_store


def test_tenant_google_disconnect_authenticates_before_deleting(monkeypatch):
    agent, token = _new_session()
    called = {"disconnect": False}
    monkeypatch.setattr(
        tenant_google,
        "disconnect",
        lambda *_args, **_kwargs: called.__setitem__("disconnect", True) or True,
    )

    with pytest.raises(HTTPException) as exc:
        onboarding_api.tenant_google_disconnect(
            agent["tenant_id"],
            "gmail",
            x_onboarding_token="wrong",
        )
    assert exc.value.status_code == 403
    assert called["disconnect"] is False

    tenant_store.mark_google_connected(agent["tenant_id"], "gmail", "ada@example.com")
    result = onboarding_api.tenant_google_disconnect(
        agent["tenant_id"],
        "gmail",
        x_onboarding_token=token,
    )

    assert called["disconnect"] is True
    assert result["disconnected"] is True
    assert result["agent"]["gmail_connected_email"] == ""


def test_tenant_voice_clone_cleanup_runs_when_cloud_attach_fails(monkeypatch):
    agent, token = _new_session()
    deleted: list[str] = []

    monkeypatch.setenv("ENABLE_PUBLIC_VOICE_CLONING", "true")
    monkeypatch.setattr(
        onboarding_api,
        "_clone_gradium_voice",
        lambda **_kwargs: ("gradium-new-voice", "Ada voice"),
    )
    monkeypatch.setattr(
        onboarding_api,
        "_delete_gradium_voice",
        lambda voice_id: deleted.append(voice_id) or {"deleted": True},
    )
    monkeypatch.setattr(
        tenant_store,
        "update_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            tenant_store.TenantStoreUnavailable("cloud write failed")
        ),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            onboarding_api.create_tenant_voice_clone(
                agent["tenant_id"],
                file=_FakeUpload(),  # type: ignore[arg-type]
                consent=True,
                voice_name="Ada voice",
                duration_seconds=30,
                x_onboarding_token=token,
            )
        )

    assert exc.value.status_code == 503
    assert deleted == ["gradium-new-voice"]


def test_tenant_voice_clone_rejects_short_samples_before_gradium(monkeypatch):
    agent, token = _new_session()

    monkeypatch.setenv("ENABLE_PUBLIC_VOICE_CLONING", "true")
    monkeypatch.setenv("GRADIUM_API_KEY", "test-key")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            onboarding_api.create_tenant_voice_clone(
                agent["tenant_id"],
                file=_FakeUpload(),  # type: ignore[arg-type]
                consent=True,
                voice_name="Ada voice",
                duration_seconds=4,
                x_onboarding_token=token,
            )
        )

    assert exc.value.status_code == 422


def test_tenant_voice_clone_reset_deletes_gradium_clone_and_clears_metadata(monkeypatch):
    agent, token = _new_session()
    deleted: list[str] = []
    tenant_store.update_agent(
        agent["tenant_id"],
        token,
        {
            "voice_clone_id": "gradium-private-id",
            "voice_clone_consent": True,
            "voice_clone_name": "Ada voice",
            "voice_clone_status": "ready",
        },
    )
    monkeypatch.setenv("ENABLE_PUBLIC_VOICE_CLONING", "true")
    monkeypatch.setattr(
        onboarding_api,
        "_delete_gradium_voice",
        lambda voice_id: deleted.append(voice_id) or {"deleted": True},
    )

    result = onboarding_api.delete_tenant_voice_clone(
        agent["tenant_id"],
        x_onboarding_token=token,
    )
    status = tenant_store.authenticate(agent["tenant_id"], token)

    assert result["ok"] is True
    assert deleted == ["gradium-private-id"]
    assert result["agent"]["voice_clone_status"] == "not_started"
    assert result["agent"]["voice_clone_name"] == ""
    assert status["voice_clone_id"] == ""
    assert status["voice_clone_consent"] is False


def test_public_agent_never_returns_private_clone_id(local_tenant_api):
    agent, token = _new_session()
    tenant_store.update_agent(
        agent["tenant_id"],
        token,
        {
            "voice_clone_id": "gradium-private-id",
            "voice_clone_name": "Ada voice",
            "voice_clone_status": "ready",
        },
    )
    status = onboarding_api.get_tenant_session(agent["tenant_id"], x_onboarding_token=token)

    assert "voice_clone_id" not in status["agent"]
    assert "gradium-private-id" in json.loads(local_tenant_api.read_text())[
        agent["record_id"]
    ]["voice_clone_id"]


def test_oauth_callback_escapes_reflected_error():
    """The unauthenticated OAuth callback must not reflect query input as HTML."""
    from starlette.testclient import TestClient

    client = TestClient(onboarding_api.app)
    payload = "<script>alert(1)</script>"
    response = client.get(
        "/api/onboarding/google/gmail/callback",
        params={"error": payload},
    )

    assert response.status_code == 400
    assert payload not in response.text
    assert "&lt;script&gt;" in response.text


def test_oauth_result_html_escapes_message_and_service():
    """Direct unit guard so the escaping cannot be silently dropped."""
    rendered = onboarding_api._oauth_result_html(
        False,
        "<img src=x onerror=alert(1)>",
        service="<b>Gmail</b>",
        event="gmail-oauth-done",
    )

    assert "<img src=x onerror=alert(1)>" not in rendered
    assert "<b>Gmail</b>" not in rendered
    assert "&lt;img" in rendered


@pytest.mark.skipif(
    not hasattr(onboarding_api, "spa_fallback"),
    reason="SPA catch-all only registers when web/dist exists.",
)
def test_spa_fallback_blocks_path_traversal():
    """A `..` traversal must never escape the built web root (no arbitrary read)."""
    from pathlib import Path

    dist_root = onboarding_api._DIST.resolve()
    index = (onboarding_api._DIST / "index.html").resolve()

    for attack in ("../../server/onboarding_api.py", "../../server/.env", "../../../etc/hosts"):
        served = Path(onboarding_api.spa_fallback(attack).path).resolve()
        assert served == index, f"traversal {attack!r} escaped to {served}"
        assert served.is_relative_to(dist_root)
