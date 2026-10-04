"""Tests for the ElevenLabs Conversational AI integration (no network calls)."""

import asyncio
import json

import pytest

import elevenlabs_agent
from elevenlabs_agent import (
    DEFAULT_TTS_MODEL,
    EVT_AGENT_RESPONSE_COMPLETE,
    EVT_CLIENT_TOOL_CALL,
    EVT_USER_TRANSCRIPT,
    build_agent_config,
    build_client_tool_config,
    resolve_voice_id,
)


class _FakeWebSocket:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.sent: list[str] = []
        self.closed = False
        self.close_code: int | None = None

    async def send_str(self, payload: str) -> None:
        self.sent.append(payload)
        self.events.append("ws.send")

    async def close(self) -> None:
        self.events.append("ws.close")
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Future()
        raise AssertionError("unreachable")


class _FakeSession:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.ws = _FakeWebSocket(self.events)
        self.closed = False

    async def ws_connect(self, _url: str) -> _FakeWebSocket:
        self.events.append("ws.connect")
        return self.ws

    async def close(self) -> None:
        self.events.append("session.close")
        self.closed = True


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


def test_connect_sends_supported_initiation_payload_and_starts_receiver(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(elevenlabs_agent.aiohttp, "ClientSession", lambda: session)

    async def fake_create_tools(_session, _tools):
        return []

    async def fake_create_agent(_session, _config):
        return "agent-test"

    async def fake_delete_agent(_session, _agent_id):
        return None

    monkeypatch.setattr(elevenlabs_agent, "create_tools", fake_create_tools)
    monkeypatch.setattr(elevenlabs_agent, "create_agent", fake_create_agent)
    monkeypatch.setattr(elevenlabs_agent, "delete_agent", fake_delete_agent)

    async def scenario():
        service = elevenlabs_agent.ElevenLabsConvAIService(
            system_prompt="prompt",
            first_message="hello",
            tools=[],
            voice_id="voice-test",
        )
        await service._connect_task
        await asyncio.sleep(0)

        assert json.loads(session.ws.sent[0]) == {
            "type": "conversation_initiation_client_data",
        }
        assert service._recv_task is not None
        assert not service._recv_task.done()
        recv_task = service._recv_task
        await service.close()
        await asyncio.gather(recv_task, return_exceptions=True)

    asyncio.run(scenario())


def test_close_awaits_agent_delete_before_session_close(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(elevenlabs_agent.aiohttp, "ClientSession", lambda: session)

    async def fake_create_tools(_session, _tools):
        return []

    async def fake_create_agent(_session, _config):
        return "agent-test"

    async def fake_delete_agent(_session, _agent_id):
        session.events.append(f"agent.delete:{session.closed}")

    monkeypatch.setattr(elevenlabs_agent, "create_tools", fake_create_tools)
    monkeypatch.setattr(elevenlabs_agent, "create_agent", fake_create_agent)
    monkeypatch.setattr(elevenlabs_agent, "delete_agent", fake_delete_agent)

    async def scenario():
        service = elevenlabs_agent.ElevenLabsConvAIService(
            system_prompt="prompt",
            first_message="hello",
            tools=[],
            voice_id="voice-test",
        )
        await service._connect_task
        await asyncio.sleep(0)
        recv_task = service._recv_task
        assert recv_task is not None
        await service.close()
        await asyncio.gather(recv_task, return_exceptions=True)

    asyncio.run(scenario())

    assert session.events[-3:] == ["ws.close", "agent.delete:False", "session.close"]


def test_resolve_voice_id_requires_consent(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "clone_9")
    monkeypatch.setenv("OWNER_VOICE_CONSENT", "false")
    monkeypatch.setenv("ELEVENLABS_PRESET_VOICE_ID", "preset_test")
    assert resolve_voice_id() == "preset_test"


def test_resolve_voice_id_uses_clone_with_consent(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "clone_9")
    monkeypatch.setenv("OWNER_VOICE_CONSENT", "true")
    monkeypatch.setenv("ELEVENLABS_PRESET_VOICE_ID", "preset_test")
    assert resolve_voice_id() == "clone_9"


def test_resolve_voice_id_tenant_clone_wins(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "clone_global")
    monkeypatch.setenv("OWNER_VOICE_CONSENT", "true")
    monkeypatch.setenv("ELEVENLABS_PRESET_VOICE_ID", "preset_test")
    assert resolve_voice_id(
        {
            "voice_clone_id": "clone_tenant",
            "voice_clone_provider": "elevenlabs",
            "voice_clone_consent": True,
        }
    ) == "clone_tenant"


def test_resolve_voice_id_owner_tenant_uses_global_elevenlabs_clone_over_gradium(
    monkeypatch,
):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "clone_global")
    monkeypatch.setenv("OWNER_VOICE_CONSENT", "true")
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "+1 (650) 555-0100")
    monkeypatch.setenv("ELEVENLABS_PRESET_VOICE_ID", "preset_test")

    assert resolve_voice_id(
        {
            "owner_phone": "6505550100",
            "voice_clone_id": "GRA1234567890123",
            "voice_clone_consent": True,
        }
    ) == "clone_global"


def test_resolve_voice_id_foreign_tenant_does_not_inherit_global_clone(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "clone_global")
    monkeypatch.setenv("OWNER_VOICE_CONSENT", "true")
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "+16505550100")
    monkeypatch.setenv("ELEVENLABS_PRESET_VOICE_ID", "preset_test")

    assert resolve_voice_id(
        {
            "owner_phone": "+14155550123",
            "voice_clone_id": "GRA1234567890123",
            "voice_clone_consent": True,
        }
    ) == "preset_test"


@pytest.mark.parametrize(
    "runtime",
    [
        {"owner_phone": "+16505550100", "voice_clone_consent": False},
        {"voice_clone_consent": True},
    ],
)
def test_resolve_voice_id_tenant_runtime_requires_owner_and_tenant_consent(
    monkeypatch,
    runtime,
):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "clone_global")
    monkeypatch.setenv("OWNER_VOICE_CONSENT", "true")
    monkeypatch.setenv("OWNER_PHONE_NUMBER", "+16505550100")
    monkeypatch.setenv("ELEVENLABS_PRESET_VOICE_ID", "preset_test")

    assert resolve_voice_id(runtime) == "preset_test"


@pytest.mark.parametrize("kwargs", [dict(), dict(bad="x")])
def test_tool_config_ignores_unknown_doc_blocks(kwargs):
    config = build_client_tool_config(_fake_tool)["tool_config"]
    assert config["parameters"]["type"] == "object"
