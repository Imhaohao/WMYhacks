"""Tests for the ElevenLabs Conversational AI integration (no network calls)."""

import asyncio
import json
from typing import Any, cast

import pytest

import elevenlabs_agent
from elevenlabs_agent import (
    DEFAULT_TTS_MODEL,
    EVT_AGENT_RESPONSE_COMPLETE,
    EVT_CLIENT_TOOL_CALL,
    EVT_USER_TRANSCRIPT,
    build_agent_config,
    build_client_tool_config,
    create_tools,
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


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return None

    async def json(self):
        return self.payload

    async def text(self):
        return json.dumps(self.payload)


class _ToolSession:
    def __init__(self) -> None:
        self.created_configs: list[dict] = []
        self.next_id = 1

    def post(self, _url: str, *, headers: dict, json: dict) -> _FakeResponse:
        assert headers["xi-api-key"] == "test-key"
        self.created_configs.append(json)
        tool_id = f"tool-{self.next_id}"
        self.next_id += 1
        return _FakeResponse({"id": tool_id})


async def _fake_tool(params, name: str, urgent: bool = False, note: str = "") -> None:
    """Store a fake message.

    Args:
        name: Who called.
        urgent: Whether it is urgent.
    """
    await params.result_callback({"ok": True})


async def _failing_tool(params) -> None:
    """Raise from a fake tool."""
    raise RuntimeError("tool failed")


async def _blocking_tool(params) -> None:
    """Wait until the service cancels this tool."""
    await asyncio.Future()


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


def test_agent_config_disables_voice_recording():
    config = build_agent_config(
        system_prompt="p", first_message="", voice_id="v", tool_ids=[]
    )
    assert config["platform_settings"]["privacy"]["record_voice"] is False


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


def test_create_tools_reuses_mixed_cached_and_new_ids_without_duplicates(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    original_cache = elevenlabs_agent._tool_id_cache.copy()
    elevenlabs_agent._tool_id_cache.clear()
    cached_key = elevenlabs_agent._schema_cache_key(build_client_tool_config(_fake_tool))
    elevenlabs_agent._tool_id_cache[cached_key] = "tool-cached"
    session = _ToolSession()

    async def scenario():
        first = await create_tools(cast(Any, session), [_fake_tool, _failing_tool])
        second = await create_tools(cast(Any, session), [_fake_tool, _failing_tool])
        return first, second

    try:
        first, second = asyncio.run(scenario())
    finally:
        elevenlabs_agent._tool_id_cache.clear()
        elevenlabs_agent._tool_id_cache.update(original_cache)

    assert first == ["tool-cached", "tool-1"]
    assert second == first
    assert len(session.created_configs) == 1


def _patch_connected_service(monkeypatch, tools):
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
            tools=tools,
            voice_id="voice-test",
        )
        await service._connect_task
        await asyncio.sleep(0)
        return service

    return session, scenario


def test_tool_dispatch_success_sends_result(monkeypatch):
    session, scenario = _patch_connected_service(monkeypatch, [_fake_tool])

    async def run():
        service = await scenario()
        try:
            await service._dispatch_tool("_fake_tool", "call-1", {"name": "caller"})
        finally:
            await service.close()

    asyncio.run(run())

    payload = json.loads(session.ws.sent[-1])
    assert payload["type"] == "client_tool_result"
    assert payload["tool_call_id"] == "call-1"
    assert json.loads(payload["result"]) == {"ok": True}
    assert payload["is_error"] is False


def test_tool_dispatch_exception_sends_error_result(monkeypatch):
    session, scenario = _patch_connected_service(monkeypatch, [_failing_tool])

    async def run():
        service = await scenario()
        try:
            await service._dispatch_tool("_failing_tool", "call-2", {})
        finally:
            await service.close()

    asyncio.run(run())

    payload = json.loads(session.ws.sent[-1])
    assert payload["tool_call_id"] == "call-2"
    assert json.loads(payload["result"])["error"] == "tool failed"
    assert payload["is_error"] is True


def test_tool_dispatch_timeout_sends_error_result(monkeypatch):
    monkeypatch.setattr(elevenlabs_agent, "TOOL_DISPATCH_TIMEOUT_SECS", 0.01)
    session, scenario = _patch_connected_service(monkeypatch, [_blocking_tool])

    async def run():
        service = await scenario()
        try:
            await service._dispatch_tool("_blocking_tool", "call-3", {})
        finally:
            await service.close()

    asyncio.run(run())

    payload = json.loads(session.ws.sent[-1])
    assert payload["tool_call_id"] == "call-3"
    assert json.loads(payload["result"]) == {"ok": False, "error": "tool timed out"}
    assert payload["is_error"] is True


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


def test_connect_failure_closes_http_session(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(elevenlabs_agent.aiohttp, "ClientSession", lambda: session)

    async def fake_create_tools(_session, _tools):
        return []

    async def failing_create_agent(_session, _config):
        raise RuntimeError("agent create failed")

    monkeypatch.setattr(elevenlabs_agent, "create_tools", fake_create_tools)
    monkeypatch.setattr(elevenlabs_agent, "create_agent", failing_create_agent)

    async def scenario():
        service = elevenlabs_agent.ElevenLabsConvAIService(
            system_prompt="prompt",
            first_message="hello",
            tools=[],
            voice_id="voice-test",
        )
        await service._connect_task
        assert service._closing is True

    asyncio.run(scenario())

    assert session.closed is True


def test_close_finishes_after_caller_cancellation_during_agent_delete(monkeypatch):
    session, scenario = _patch_connected_service(monkeypatch, [])
    delete_started = asyncio.Event()
    release_delete = asyncio.Event()

    async def slow_delete(_session, _agent_id):
        delete_started.set()
        await release_delete.wait()
        session.events.append(f"agent.delete:{session.closed}")

    monkeypatch.setattr(elevenlabs_agent, "delete_agent", slow_delete)

    async def run():
        service = await scenario()
        close_task = asyncio.create_task(service.close())
        await delete_started.wait()
        close_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_task

        assert session.closed is False
        release_delete.set()
        await service.close()

    asyncio.run(run())

    assert session.closed is True
    assert session.events[-3:] == ["ws.close", "agent.delete:False", "session.close"]


def test_cancel_frame_closes_resources_and_awaits_pending_tasks(monkeypatch):
    session, scenario = _patch_connected_service(monkeypatch, [_blocking_tool])

    async def run():
        service = await scenario()
        recv_task = service._recv_task
        assert recv_task is not None
        service._handle_event(
            {
                "type": EVT_CLIENT_TOOL_CALL,
                "client_tool_call": {
                    "tool_name": "_blocking_tool",
                    "tool_call_id": "call-cancel",
                    "parameters": {},
                },
            }
        )
        await asyncio.sleep(0)
        assert service._tool_tasks
        tool_task = next(iter(service._tool_tasks))

        await service.process_frame(
            elevenlabs_agent.CancelFrame(), elevenlabs_agent.FrameDirection.DOWNSTREAM
        )

        assert recv_task.done()
        assert tool_task.done()
        await service.cleanup()

    asyncio.run(run())

    assert session.ws.closed is True
    assert session.closed is True
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
