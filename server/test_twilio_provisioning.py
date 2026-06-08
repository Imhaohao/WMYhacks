"""Hermetic tests for Twilio number webhook wiring."""

from __future__ import annotations

import twilio_provisioning


class _Response:
    def __init__(self, payload: dict, *, ok: bool = True, status_code: int = 200) -> None:
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


def test_voice_webhook_url_uses_public_base_and_normalized_path(monkeypatch):
    monkeypatch.setenv("TWILIO_VOICE_PATH", "incoming")

    assert twilio_provisioning.voice_webhook_url("https://voice.example.test/") == (
        "https://voice.example.test/incoming"
    )


def test_wire_inbound_webhook_requires_configuration(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)

    result = twilio_provisioning.wire_inbound_webhook("+16505550101")

    assert result["ok"] is False
    assert "credentials" in result["detail"]


def test_wire_inbound_webhook_updates_existing_twilio_number(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://voice.example.test")
    calls = []

    def fake_get(url, *, params, auth, timeout):
        calls.append(("get", url, params, auth, timeout))
        return _Response({"incoming_phone_numbers": [{"sid": "PN-test"}]})

    def fake_post(url, *, data, auth, timeout):
        calls.append(("post", url, data, auth, timeout))
        return _Response({})

    monkeypatch.setattr(twilio_provisioning.requests, "get", fake_get)
    monkeypatch.setattr(twilio_provisioning.requests, "post", fake_post)

    result = twilio_provisioning.wire_inbound_webhook("+16505550101")

    assert result == {
        "ok": True,
        "voice_url": "https://voice.example.test/",
        "detail": "Voice webhook set to https://voice.example.test/",
    }
    assert calls[0][0] == "get"
    assert calls[0][2] == {"PhoneNumber": "+16505550101"}
    assert calls[1][0] == "post"
    assert calls[1][2] == {"VoiceUrl": "https://voice.example.test/", "VoiceMethod": "POST"}


def test_wire_configured_demo_number_uses_twilio_phone_number(monkeypatch):
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+16505550101")
    called = {}

    def fake_wire(phone, public_base_url=None):
        called["phone"] = phone
        called["public_base_url"] = public_base_url
        return {"ok": True}

    monkeypatch.setattr(twilio_provisioning, "wire_inbound_webhook", fake_wire)

    result = twilio_provisioning.wire_configured_demo_number("https://voice.example.test")

    assert result == {"ok": True}
    assert called == {
        "phone": "+16505550101",
        "public_base_url": "https://voice.example.test",
    }


def test_wire_configured_demo_number_requires_phone(monkeypatch):
    monkeypatch.delenv("TWILIO_PHONE_NUMBER", raising=False)

    result = twilio_provisioning.wire_configured_demo_number()

    assert result["ok"] is False
    assert "TWILIO_PHONE_NUMBER" in result["detail"]


def test_available_local_numbers_searches_twilio_inventory(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    calls = []

    def fake_get(url, *, params, auth, timeout):
        calls.append((url, params, auth, timeout))
        return _Response(
            {
                "available_phone_numbers": [
                    {
                        "phone_number": "+16505550177",
                        "capabilities": {"voice": True, "sms": True},
                    }
                ]
            }
        )

    monkeypatch.setattr(twilio_provisioning.requests, "get", fake_get)

    result = twilio_provisioning.available_local_numbers(
        country="US",
        area_code="650",
        contains="177",
        near_number="+16505550199",
        limit=3,
    )

    assert result["ok"] is True
    assert result["numbers"][0]["phone_number"] == "+16505550177"
    assert calls == [
        (
            "https://api.twilio.com/2010-04-01/Accounts/AC-test/AvailablePhoneNumbers/US/Local.json",
            {
                "PageSize": "3",
                "AreaCode": "650",
                "Contains": "177",
                "NearNumber": "+16505550199",
                "SmsEnabled": "true",
                "VoiceEnabled": "true",
            },
            ("AC-test", "secret"),
            12,
        )
    ]


def test_purchase_phone_number_sets_voice_url(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://voice.example.test")
    calls = []

    def fake_post(url, *, data, auth, timeout):
        calls.append((url, data, auth, timeout))
        return _Response({"phone_number": "+16505550177", "sid": "PN-test"})

    monkeypatch.setattr(twilio_provisioning.requests, "post", fake_post)

    result = twilio_provisioning.purchase_phone_number(
        "+16505550177",
        friendly_name="Gotchu tenant abc123",
    )

    assert result["ok"] is True
    assert result["agent_phone"] == "+16505550177"
    assert result["voice_url"] == "https://voice.example.test/"
    assert calls == [
        (
            "https://api.twilio.com/2010-04-01/Accounts/AC-test/IncomingPhoneNumbers.json",
            {
                "PhoneNumber": "+16505550177",
                "VoiceUrl": "https://voice.example.test/",
                "VoiceMethod": "POST",
                "FriendlyName": "Gotchu tenant abc123",
            },
            ("AC-test", "secret"),
            12,
        )
    ]


def test_purchase_and_wire_local_number_skips_address_required_candidates(monkeypatch):
    monkeypatch.setattr(
        twilio_provisioning,
        "available_local_numbers",
        lambda **_kwargs: {
            "ok": True,
            "numbers": [
                {
                    "phone_number": "+16505550177",
                    "address_requirements": "local",
                    "capabilities": {"voice": True},
                },
                {
                    "phone_number": "+16505550178",
                    "address_requirements": "none",
                    "capabilities": {"voice": True},
                    "locality": "San Mateo",
                    "region": "CA",
                },
            ],
        },
    )
    purchased = []
    monkeypatch.setattr(
        twilio_provisioning,
        "purchase_phone_number",
        lambda phone, **kwargs: purchased.append((phone, kwargs))
        or {
            "ok": True,
            "agent_phone": phone,
            "voice_url": "https://voice.example.test/",
        },
    )

    result = twilio_provisioning.purchase_and_wire_local_number(
        country="US",
        area_code="650",
        public_base_url="https://voice.example.test",
        friendly_name="Gotchu tenant",
    )

    assert result["ok"] is True
    assert result["agent_phone"] == "+16505550178"
    assert result["candidate"] == {
        "phone_number": "+16505550178",
        "locality": "San Mateo",
        "region": "CA",
    }
    assert purchased == [
        (
            "+16505550178",
            {
                "public_base_url": "https://voice.example.test",
                "friendly_name": "Gotchu tenant",
            },
        )
    ]


def test_purchase_and_wire_local_number_reports_no_safe_candidate(monkeypatch):
    monkeypatch.setattr(
        twilio_provisioning,
        "available_local_numbers",
        lambda **_kwargs: {
            "ok": True,
            "numbers": [
                {
                    "phone_number": "+16505550177",
                    "address_requirements": "local",
                    "capabilities": {"voice": True},
                }
            ],
        },
    )

    result = twilio_provisioning.purchase_and_wire_local_number()

    assert result["ok"] is False
    assert "required an address" in result["detail"]
