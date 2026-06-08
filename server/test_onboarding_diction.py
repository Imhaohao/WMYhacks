"""Hermetic tests for owner diction onboarding."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import onboarding_api
import owner_config


@pytest.fixture
def isolated_owner_config(tmp_path, monkeypatch):
    config_path = tmp_path / "owner_config.json"
    monkeypatch.setattr(owner_config, "CONFIG_PATH", config_path)
    monkeypatch.setenv("ALLOW_OWNER_PROVISIONING", "true")
    return config_path


def _profile_body() -> onboarding_api.DictionProfileBody:
    return onboarding_api.DictionProfileBody(
        samples=[
            "yo bro, gotchu. i'll call after lunch. jane@example.com",
            "yo bro, gotchu. i'll circle back soon. +1 (555) 123-4567",
            "yo bro, gotchu. i'll text you later. sk-secretvalue123456",
        ],
        phrases=["talk soon"],
    )


def test_preview_derives_bounded_profile_without_raw_sensitive_values():
    profile = onboarding_api.preview_diction_profile(_profile_body())
    rendered = json.dumps(profile)

    assert profile["ready"] is True
    assert profile["sample_count"] == 3
    assert "yo bro" in profile["phrases"]
    assert "talk soon" in profile["phrases"]
    assert "jane@example.com" not in rendered
    assert "555" not in rendered
    assert "sk-secretvalue123456" not in rendered


def test_save_persists_only_derived_profile_and_persona_uses_it(isolated_owner_config):
    onboarding_api.save_diction_profile(_profile_body())
    stored = owner_config.load_owner_config()
    serialized = json.dumps(stored)

    assert stored["diction_examples"] == ""
    assert "yo bro" in stored["diction_profile_phrases"]
    assert "talk soon" in stored["diction_profile_phrases"]
    assert "jane@example.com" not in serialized
    assert "555" not in serialized
    assert "sk-secretvalue123456" not in serialized

    persona = owner_config.build_persona_context()
    assert "Owner diction profile:" in persona
    assert "yo bro" in persona
    assert "naturally and sparingly" in persona


def test_save_rejects_public_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_config, "CONFIG_PATH", tmp_path / "owner_config.json")
    monkeypatch.setenv("ALLOW_OWNER_PROVISIONING", "false")

    with pytest.raises(HTTPException) as exc:
        onboarding_api.save_diction_profile(_profile_body())

    assert exc.value.status_code == 403


def test_preview_requires_three_replies():
    with pytest.raises(HTTPException) as exc:
        onboarding_api.preview_diction_profile(
            onboarding_api.DictionProfileBody(samples=["gotchu", "talk soon"])
        )

    assert exc.value.status_code == 422
