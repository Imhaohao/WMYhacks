"""Tests for the ElevenLabs Conversational AI integration (no network calls)."""

import pytest

from elevenlabs_agent import (
    DEFAULT_FALLBACK_VOICE_ID,
    DEFAULT_TTS_MODEL,
    EVT_AGENT_RESPONSE_COMPLETE,
    EVT_CLIENT_TOOL_CALL,
    EVT_USER_TRANSCRIPT,
    build_agent_config,
    build_client_tool_config,
    resolve_voice_id,
)


async def _fake_tool(params, name: str, urgent: bool = False, note: str = "") -> None:
    """Store a fake message.

    Args:
        name: Who called.
        urgent: Whether it is urgent.
    """
    await params.result_callback({"ok": True})


def test_tool_config_drops_params_param_and_marks_required():
    config = build_client_tool_config(_fake_tool)["tool_config"]
    props = config["parameters"]
    assert config["type"] == "client"
    assert config["name"] == "_fake_tool"
    assert config["expects_response"] is True
    assert props["properties"]["name"]["type"] == "string"
    assert props["required"] == ["name"]
    assert "urgent" in props["properties"]
    assert "urgent" not in props["required"]


def test_tool_config_description_and_param_docs():
    config = build_client_tool_config(_fake_tool)["tool_config"]
    assert "Store a fake message" in config["description"]
    assert "Args:" not in config["description"]
    assert config["parameters"]["properties"]["name"]["description"] == "Who called."


def test_agent_config_defaults_to_flash_and_wires_voice():
    config = build_agent_config(
        system_prompt="be nice",
        first_message="hi",
        voice_id="voice_123",
        tool_ids=["tool_1"],
    )
    conv = config["conversation_config"]
    assert conv["tts"]["model_id"] == DEFAULT_TTS_MODEL
    assert conv["tts"]["voice_id"] == "voice_123"
    assert conv["agent"]["prompt"]["prompt"] == "be nice"
    assert conv["agent"]["prompt"]["tool_ids"] == ["tool_1"]
    assert conv["agent"]["first_message"] == "hi"


def test_agent_config_includes_end_call_system_tool_and_core_events():
    config = build_agent_config(
        system_prompt="p", first_message="", voice_id="v", tool_ids=[]
    )
    built_in = config["conversation_config"]["agent"]["prompt"]["built_in_tools"]
    assert "end_call" in built_in
    events = config["conversation_config"]["conversation"]["client_events"]
    for required in (
        "audio",
        EVT_USER_TRANSCRIPT,
        EVT_AGENT_RESPONSE_COMPLETE,
        EVT_CLIENT_TOOL_CALL,
    ):
        assert required in events


def test_agent_config_asr_keywords_capped():
    config = build_agent_config(
        system_prompt="p", first_message="", voice_id="v", tool_ids=[],
        asr_keywords=[f"kw{i}" for i in range(60)],
    )
    assert len(config["conversation_config"]["asr"]["keywords"]) == 50


def test_resolve_voice_id_requires_consent(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "clone_9")
    monkeypatch.setenv("OWNER_VOICE_CONSENT", "false")
    assert resolve_voice_id() == DEFAULT_FALLBACK_VOICE_ID


def test_resolve_voice_id_uses_clone_with_consent(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "clone_9")
    monkeypatch.setenv("OWNER_VOICE_CONSENT", "true")
    assert resolve_voice_id() == "clone_9"


def test_resolve_voice_id_tenant_clone_wins(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "clone_global")
    monkeypatch.setenv("OWNER_VOICE_CONSENT", "true")
    assert resolve_voice_id({"voice_clone_id": "clone_tenant", "voice_clone_consent": True}) == (
        "clone_tenant"
    )


@pytest.mark.parametrize("kwargs", [dict(), dict(bad="x")])
def test_tool_config_ignores_unknown_doc_blocks(kwargs):
    config = build_client_tool_config(_fake_tool)["tool_config"]
    assert config["parameters"]["type"] == "object"
