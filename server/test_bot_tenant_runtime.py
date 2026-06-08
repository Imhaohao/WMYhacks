"""Tenant routing regressions for the Twilio voice runtime."""

from __future__ import annotations

import tenant_runtime


def test_twilio_parameter_extraction_handles_webhook_and_stream_names():
    info = tenant_runtime.call_info_from_twilio_parameters(
        {
            "From": "+16505550199",
            "To": "+16506444731",
            "ForwardedFrom": "+16505550100",
        }
    )

    assert info == {
        "from_number": "+16505550199",
        "to_number": "+16506444731",
        "forwarded_from": "+16505550100",
    }


def test_twilio_stream_twiml_passes_forwarding_metadata_to_websocket():
    twiml = tenant_runtime.build_twilio_stream_twiml(
        "https://voice.example.test",
        {
            "From": "+16505550199",
            "To": "+16506444731",
            "ForwardedFrom": "+16505550100",
        },
    )

    assert '<Stream url="wss://voice.example.test/ws">' in twiml
    assert '<Parameter name="From" value="+16505550199"/>' in twiml
    assert '<Parameter name="To" value="+16506444731"/>' in twiml
    assert '<Parameter name="ForwardedFrom" value="+16505550100"/>' in twiml


def test_tenant_runtime_routes_shared_forwarded_calls_by_owner_phone(monkeypatch):
    runtime = {
        "tenant_id": "tenant-one",
        "owner_phone": "+16505550100",
        "forwarding_phone": "+16506444731",
        "routing_mode": "shared_forwarded_from",
    }
    monkeypatch.setattr(
        tenant_runtime.tenant_store,
        "get_agent_by_owner_phone",
        lambda _phone: runtime,
    )

    def fail_dedicated_lookup(_phone):
        raise AssertionError("shared forwarded calls should not require a dedicated lookup")

    monkeypatch.setattr(tenant_runtime.tenant_store, "get_agent_by_phone", fail_dedicated_lookup)

    resolved = tenant_runtime.resolve_for_call("+16506444731", forwarded_from="+16505550100")

    assert resolved == runtime


def test_tenant_runtime_falls_back_to_dedicated_number_when_forwarding_mismatches(monkeypatch):
    dedicated = {"tenant_id": "tenant-two", "agent_phone": "+16505550177"}
    monkeypatch.setattr(
        tenant_runtime.tenant_store,
        "get_agent_by_owner_phone",
        lambda _phone: {
            "tenant_id": "wrong-tenant",
            "forwarding_phone": "+16506440000",
        },
    )
    monkeypatch.setattr(
        tenant_runtime.tenant_store,
        "get_agent_by_phone",
        lambda _phone: dedicated,
    )

    resolved = tenant_runtime.resolve_for_call("+16505550177", forwarded_from="+16505550100")

    assert resolved == dedicated
