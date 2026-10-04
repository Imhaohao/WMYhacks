"""ElevenLabs Conversational AI — full realtime voice stack for the voicemail bot.

Replaces the STT → LLM → TTS cascade with ElevenLabs' hosted Conversational AI
agent (``eleven_flash_v2`` realtime TTS + managed LLM + Scribe realtime ASR)
running end-to-end on their infra, streaming over a single WebSocket:

    wss://api.elevenlabs.io/v1/convai/conversation?agent_id=<id>

One agent is created per call (so the system prompt carries the full persona
context, caller name and assistant-mode banner), client tools are created once
per schema and cached in-process.  Tool invocations arrive as ``client_tool_call``
events and are dispatched to the same P1/P2/P3 function closures the Pipecat
pipeline used, with results returned as ``client_tool_result``.

Voice cloning helper (owner consent required):

    python -m elevenlabs_agent clone /path/to/voice.m4a --name "Owner clone"
"""

import asyncio
import base64
import inspect
import json
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    EndTaskFrame,
    Frame,
    InputAudioRawFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import tenant_runtime as tenant_runtime_module

_ENV_DIR = Path(__file__).resolve().parent
load_dotenv(_ENV_DIR / ".env", override=True)
load_dotenv(_ENV_DIR / ".env.local", override=True)

API_BASE = "https://api.elevenlabs.io"
WS_CONVERSATION_URL = "wss://api.elevenlabs.io/v1/convai/conversation"
DEFAULT_TTS_MODEL = "eleven_flash_v2"  # lowest-latency realtime flash model (English agents)
# eleven_flash_v2_5 is also realtime but rejected for English agents on this account.
DEFAULT_LLM = "gemini-2.5-flash"  # fast hosted LLM; override with ELEVENLABS_AGENT_LLM
DEFAULT_FALLBACK_VOICE_ID = "cjVigY5qzO86Huf0OWal"  # ElevenLabs preset if no clone
MAX_TOOL_RESULT_CHARS = 12000
TOOL_DISPATCH_TIMEOUT_SECS = 10.0

# Server events that carry the conversation (see
# https://elevenlabs.io/docs/eleven-agents/eleven-agents-api/websocket).
EVT_METADATA = "conversation_initiation_metadata"
EVT_AUDIO = "audio"
EVT_USER_TRANSCRIPT = "user_transcript"
EVT_AGENT_RESPONSE = "agent_response"
EVT_AGENT_RESPONSE_COMPLETE = "agent_response_complete"
EVT_CLIENT_TOOL_CALL = "client_tool_call"
EVT_INTERRUPTION = "interruption"
EVT_PING = "ping"
EVT_QUEUE_STATUS = "queue_status"
EVT_CLIENT_ERROR = "client_error"
EVT_AGENT_TOOL_RESPONSE = "agent_tool_response"


def elevenlabs_api_key() -> str:
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set — add it to server/.env")
    return key


def resolve_voice_id(tenant_runtime: dict[str, Any] | None = None) -> str:
    """Resolve an ElevenLabs voice without crossing the Gradium/provider boundary.

    Tenant onboarding currently creates Gradium clones, so the generic tenant
    ``voice_clone_id`` is accepted here only when its provider is explicitly
    ``elevenlabs``. The configured global clone is reserved for the legacy
    runtime and the configured owner's tenant call.
    """
    runtime = tenant_runtime or {}
    has_tenant_runtime = bool(runtime)
    tenant_cloned_id = str(runtime.get("voice_clone_id") or "").strip()
    tenant_provider = str(runtime.get("voice_clone_provider") or "").strip().lower()
    tenant_consent = bool(runtime.get("voice_clone_consent"))
    global_cloned_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    global_consent = os.getenv("OWNER_VOICE_CONSENT", "false").strip().lower() == "true"
    preset_id = os.getenv("ELEVENLABS_PRESET_VOICE_ID", DEFAULT_FALLBACK_VOICE_ID).strip()

    if tenant_cloned_id and tenant_provider == "elevenlabs" and tenant_consent:
        logger.info("ElevenLabs TTS: using tenant's consented ElevenLabs cloned voice")
        return tenant_cloned_id

    if has_tenant_runtime:
        if (
            tenant_runtime_module.is_configured_owner_runtime(runtime)
            and global_cloned_id
            and tenant_consent
            and global_consent
        ):
            logger.info("ElevenLabs TTS: using configured owner's consented cloned voice")
            return global_cloned_id
        if tenant_cloned_id and tenant_provider != "elevenlabs":
            logger.warning(
                "Ignoring tenant voice clone for ElevenLabs because its provider is not ElevenLabs."
            )
        return preset_id

    if global_cloned_id and global_consent:
        logger.info("ElevenLabs TTS: using owner's consented cloned voice")
        return global_cloned_id
    if global_cloned_id and not global_consent:
        logger.warning(
            "ELEVENLABS_VOICE_ID is set but OWNER_VOICE_CONSENT != true — falling back "
            "to preset voice. Set OWNER_VOICE_CONSENT=true to use the clone."
        )
    return preset_id


# ─── Tool schema extraction (Pipecat function → ElevenLabs client tool) ───────


_JSON_TYPE = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}


def _tool_description(fn: Callable) -> str:
    """Docstring body (before the ``Args:`` block) as the tool description."""
    doc = (inspect.getdoc(fn) or "").strip()
    if not doc:
        return fn.__name__
    body = doc.split("\nArgs:", 1)[0].strip()
    return (body or doc)[:2000]


def _param_description(fn: Callable, param_name: str) -> str | None:
    doc = inspect.getdoc(fn) or ""
    match = re.search(rf"^\s*{re.escape(param_name)}:\s*(.+)$", doc, re.MULTILINE)
    return match.group(1).strip() if match else None


def _annotation_json_type(annotation: Any) -> str:
    name = getattr(annotation, "__name__", str(annotation))
    return _JSON_TYPE.get(name, "string")


def build_client_tool_config(fn: Callable) -> dict[str, Any]:
    """Build the NineElevenLabs ``tool_config`` for one Pipecat tool function.

    The first parameter (``params: FunctionCallParams``) is dropped; remaining
    parameters become JSON-schema properties.  Parameters with Python defaults
    stay optional — when the LLM omits them, the default kicks in server-side
    at dispatch time.
    """
    sig = getattr(fn, "__signature__", None) or inspect.signature(fn)
    params = [p for p in sig.parameters.values() if p.name != "params"]
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in params:
        prop: dict[str, Any] = {"type": _annotation_json_type(param.annotation)}
        desc = _param_description(fn, param.name)
        if desc:
            prop["description"] = desc
        properties[param.name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(param.name)
    description = _tool_description(fn)
    return {
        "tool_config": {
            "type": "client",
            "name": fn.__name__,
            "description": description,
            "expects_response": True,
            "execution_mode": "immediate",
            "interruption_mode": "allow",
            "response_timeout_secs": 30,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }
    }


def _schema_cache_key(tool_config: dict[str, Any]) -> str:
    return json.dumps(tool_config["tool_config"], sort_keys=True, default=str)


# ─── REST helpers (aiohttp) ────────────────────────────────────────────────────

_tool_id_cache: dict[str, str] = {}


async def create_tools(
    session: aiohttp.ClientSession,
    functions: Sequence[Callable],
) -> list[str]:
    """Create (or reuse cached) client tools; return their tool_ids."""
    api_key = elevenlabs_api_key()
    configs_by_key: dict[str, dict[str, Any]] = {}
    for fn in functions:
        config = build_client_tool_config(fn)
        cache_key = _schema_cache_key(config)
        configs_by_key.setdefault(cache_key, config)

    pending = [
        (cache_key, config)
        for cache_key, config in configs_by_key.items()
        if cache_key not in _tool_id_cache
    ]

    async def _create(config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        async with session.post(
            f"{API_BASE}/v1/convai/tools",
            headers={"xi-api-key": api_key},
            json=config,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"ElevenLabs tool create failed ({resp.status}): {body[:300]}")
            data = await resp.json()
            return data["id"], config

    if pending:
        results = await asyncio.gather(
            *(_create(config) for _, config in pending), return_exceptions=True
        )
        for pending_item, result in zip(pending, results):
            cache_key = pending_item[0]
            if isinstance(result, BaseException):
                raise cast(Exception, result)
            _tool_id_cache[cache_key] = result[0]

    tool_ids = [_tool_id_cache[cache_key] for cache_key in configs_by_key]
    logger.debug("ElevenLabs: {} client tools ready", len(tool_ids))
    return tool_ids


def build_agent_config(
    *,
    system_prompt: str,
    first_message: str,
    voice_id: str,
    tool_ids: list[str],
    asr_keywords: Sequence[str] | None = None,
    tts_model: str | None = None,
    llm_id: str | None = None,
) -> dict[str, Any]:
    """Agent creation payload for POST /v1/convai/agents/create."""
    asr: dict[str, Any] = {
        "provider": "scribe_realtime",
        "quality": "high",
        "user_input_audio_format": "pcm_16000",
    }
    if asr_keywords:
        asr["keywords"] = list(asr_keywords)[:50]
    tts_model = tts_model or os.getenv("ELEVENLABS_TTS_MODEL", DEFAULT_TTS_MODEL)
    llm_id = llm_id or os.getenv("ELEVENLABS_AGENT_LLM", DEFAULT_LLM)
    return {
        "name": "Voicemail Agent",
        "platform_settings": {
            "privacy": {
                "record_voice": False,
            },
        },
        "conversation_config": {
            "agent": {
                "prompt": {
                    "prompt": system_prompt,
                    "llm": llm_id,
                    "tool_ids": tool_ids,
                    "built_in_tools": {
                        "end_call": {
                            "name": "end_call",
                            "description": (
                                "Hang up the call. Only call this after you have said "
                                "goodbye to the caller in the same turn."
                            ),
                            "params": {"system_tool_type": "end_call"},
                        }
                    },
                },
                "first_message": first_message,
                "language": "en",
                "max_conversation_duration_message": "I should let you go — goodbye!",
            },
            "asr": asr,
            "turn": {
                "turn_model": "turn_v3",
                "turn_eagerness": os.getenv("ELEVENLABS_TURN_EAGERNESS", "normal"),
                "turn_timeout": 7.0,
                "speculative_turn": True,
                "retranscribe_on_turn_timeout": True,
                "interruption_ignore_terms": [
                    "ok", "okay", "uh huh", "gotcha", "yeah", "right", "mhm", "understood",
                ],
                "soft_timeout_config": {
                    "timeout_seconds": 4.5,
                    "message": "One moment.",
                    "use_llm_generated_message": False,
                },
            },
            "tts": {
                "model_id": tts_model,
                "voice_id": voice_id,
                "agent_output_audio_format": "pcm_16000",
                "stability": 0.5,
                "similarity_boost": 0.8,
                "speed": 1.0,
            },
            "conversation": {
                "max_duration_seconds": 900,
                "client_events": [
                    EVT_METADATA, EVT_AUDIO, EVT_INTERRUPTION, EVT_USER_TRANSCRIPT,
                    EVT_AGENT_RESPONSE, EVT_AGENT_RESPONSE_COMPLETE, EVT_CLIENT_TOOL_CALL,
                    EVT_AGENT_TOOL_RESPONSE, EVT_PING, EVT_CLIENT_ERROR,
                ],
            },
        },
    }


async def create_agent(
    session: aiohttp.ClientSession,
    config: dict[str, Any],
) -> str:
    async with session.post(
        f"{API_BASE}/v1/convai/agents/create",
        headers={"xi-api-key": elevenlabs_api_key()},
        json=config,
    ) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"ElevenLabs agent create failed ({resp.status}): {body[:300]}")
        data = await resp.json()
        return data["agent_id"]


async def delete_agent(session: aiohttp.ClientSession, agent_id: str) -> None:
    try:
        async with session.delete(
            f"{API_BASE}/v1/convai/agents/{agent_id}",
            headers={"xi-api-key": elevenlabs_api_key()},
        ) as resp:
            if resp.status not in (200, 204):
                logger.warning("ElevenLabs agent delete returned {}", resp.status)
    except Exception as exc:
        logger.warning("ElevenLabs agent delete skipped: {}", exc)


# ─── Tool-call bridging ────────────────────────────────────────────────────────


class _ConvAIParams:
    """Minimal stand-in for Pipecat's FunctionCallParams.

    ``result_callback`` ships the tool's return dict back to the hosted agent
    as a ``client_tool_result`` message.  ``llm`` is a stub so shared tool
    closures that call ``params.llm.push_frame(EndTaskFrame(), ...)`` (end_call)
    run unchanged — in ConvAI mode the hang-up is driven by the hosted agent's
    system ``end_call`` tool, which closes the WebSocket after the farewell.
    """

    def __init__(self, service: "ElevenLabsConvAIService", tool_call_id: str) -> None:
        self._service = service
        self._tool_call_id = tool_call_id
        self.llm = _ConvAILLMStub(service)

    async def result_callback(
        self, result: dict[str, Any] | None = None, threads: Any = None, **_: Any
    ) -> None:
        await self._service.send_tool_result(self._tool_call_id, result or {}, is_error=False)


class _ConvAILLMStub:
    def __init__(self, service: "ElevenLabsConvAIService") -> None:
        self._service = service

    async def push_frame(self, frame: Frame, direction: Any = None) -> None:
        if isinstance(frame, (EndTaskFrame, EndFrame)):
            # The hosted agent owns the hang-up; the WebSocket close (or the
            # transport disconnecting first) ends the local pipeline.
            logger.debug("ElevenLabs: ignoring EndTaskFrame from tool (agent ends the call)")
        else:
            logger.debug("ElevenLabs: LLM stub ignoring frame {}", type(frame).__name__)


# ─── The service ───────────────────────────────────────────────────────────────


class ElevenLabsConvAIService(FrameProcessor):
    """Pipecat FrameProcessor driving one ElevenLabs Conversational AI session.

    Input:  ``InputAudioRawFrame`` (16 kHz mono s16le) → ``user_audio_chunk``.
    Output: ``TTSStartedFrame`` / ``TTSAudioRawFrame`` (PCM per server metadata)
            / ``TTSStoppedFrame`` downstream, ``TranscriptionFrame`` upstream for
            finalized user utterances and downstream for agent text.
    """

    def __init__(
        self,
        *,
        system_prompt: str,
        first_message: str,
        tools: Sequence[Callable],
        voice_id: str,
        asr_keywords: Sequence[str] | None = None,
        call_state: Any = None,
        hangup_cb: Callable[[], Any] | None = None,
        name: str = "elevenlabs_convai",
        **kwargs,
    ) -> None:
        super().__init__(name=name, **kwargs)
        self._system_prompt = system_prompt
        self._first_message = first_message
        self._tools = tools
        self._voice_id = voice_id
        self._asr_keywords = asr_keywords
        self._call_state = call_state
        self._hangup_cb = hangup_cb
        self._session = aiohttp.ClientSession()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._agent_id: str | None = None
        self._recv_task: asyncio.Task | None = None
        self._tool_tasks: set[asyncio.Task] = set()
        self._tool_by_name = {fn.__name__: fn for fn in tools}
        self._agent_speaking = False
        self._output_sample_rate = 24000
        self._messages: list[dict[str, str]] = []
        self._opened = False
        self._closing = False
        self._close_task: asyncio.Task | None = None
        self._close_owner: asyncio.Task | None = None
        self._connect_task = asyncio.ensure_future(self._connect())

    @property
    def messages(self) -> list[dict[str, str]]:
        """User/assistant transcript turns (for Cekura and finalize logging)."""
        return self._messages

    def _record_message(self, role: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self._messages.append({"role": role, "content": text})
        if self._call_state is not None and role == "user":
            transcript = self._call_state["voicemail"]["transcript"]
            self._call_state["voicemail"]["transcript"] = f"{transcript} {text}".strip()

    async def _connect(self) -> None:
        try:
            tool_ids = await create_tools(self._session, self._tools)
            config = build_agent_config(
                system_prompt=self._system_prompt,
                first_message=self._first_message,
                voice_id=self._voice_id,
                tool_ids=tool_ids,
                asr_keywords=self._asr_keywords,
            )
            self._agent_id = await create_agent(self._session, config)
            ws = await self._session.ws_connect(
                f"{WS_CONVERSATION_URL}?agent_id={self._agent_id}"
            )
            self._ws = ws
            await ws.send_str(
                json.dumps(
                    {
                        "type": "conversation_initiation_client_data",
                    }
                )
            )
            self._recv_task = asyncio.create_task(self._receive_messages())
            logger.info("ElevenLabs ConvAI session open (agent_id={})", self._agent_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("ElevenLabs ConvAI connect failed: {}", exc)
            self._opened = False
            await self.close()

    async def _receive_messages(self) -> None:
        ws = self._ws
        assert ws is not None
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("ElevenLabs WS error: {}", ws.exception())
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    self._handle_event(json.loads(msg.data))
                except Exception as exc:
                    logger.warning("ElevenLabs event handling failed ({}): {}", type(exc).__name__, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("ElevenLabs WS receive loop ended: {}", exc)
        if ws.close_code not in (None, 1000, 1001):
            logger.warning("ElevenLabs WS closed unexpectedly (code={})", ws.close_code)
        # The hosted agent closed the conversation (its end_call system tool,
        # guardrail, or queue timeout). End the local pipeline so the transport
        # disconnects — otherwise the phone line stays open in silence.
        if not self._closing:
            logger.info("ElevenLabs: agent ended the conversation — ending pipeline")
            asyncio.ensure_future(self._end_pipeline())

    async def _end_pipeline(self) -> None:
        """Hosted agent ended the call: disconnect the transport and stop the pipeline."""
        self._end_agent_turn()
        if self._hangup_cb is not None:
            await self._hangup_cb()
        await self.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)

    def _handle_event(self, event: dict[str, Any]) -> None:
        evt_type = event.get("type")
        if evt_type == EVT_METADATA:
            meta = event.get("conversation_initiation_metadata_event", {})
            out_format = str(meta.get("agent_output_audio_format") or "pcm_24000")
            rate_match = re.search(r"(\d+)", out_format)
            self._output_sample_rate = int(rate_match.group(1)) if rate_match else 24000
            self._opened = True
        elif evt_type == EVT_AUDIO:
            self._on_agent_audio(event.get("audio_event", {}))
        elif evt_type == EVT_USER_TRANSCRIPT:
            text = (event.get("user_transcription_event", {}) or {}).get("user_transcript", "")
            self._record_message("user", text)
            self._push_transcript(text, user_id="user", direction=FrameDirection.UPSTREAM)
        elif evt_type == EVT_AGENT_RESPONSE:
            text = (event.get("agent_response_event", {}) or {}).get("agent_response", "")
            self._record_message("assistant", text)
            self._push_transcript(text, user_id="assistant", direction=FrameDirection.DOWNSTREAM)
        elif evt_type in (EVT_AGENT_RESPONSE_COMPLETE, EVT_INTERRUPTION):
            self._end_agent_turn()
        elif evt_type == EVT_CLIENT_TOOL_CALL:
            call = event.get("client_tool_call", {})
            self._schedule_tool_dispatch(
                call.get("tool_name", ""),
                call.get("tool_call_id", ""),
                call.get("parameters", {}),
            )
        elif evt_type == EVT_PING:
            event_id = (event.get("ping_event", {}) or {}).get("event_id")
            if event_id is not None:
                asyncio.ensure_future(self._send_json({"type": "pong", "event_id": event_id}))
        elif evt_type == EVT_QUEUE_STATUS:
            status = (event.get("queue_status_event", {}) or {}).get("status")
            if status:
                logger.info("ElevenLabs queue status: {}", status)
        elif evt_type == EVT_CLIENT_ERROR:
            err = event.get("error_event", {}) or {}
            logger.warning("ElevenLabs client_error: {}", err)
        elif evt_type == EVT_AGENT_TOOL_RESPONSE:
            self._on_agent_tool_response(event.get("agent_tool_response", {}))

    def _push_transcript(self, text: str, user_id: str, direction: FrameDirection) -> None:
        if not text or self._closing:
            return
        frame = TranscriptionFrame(text, user_id=user_id, timestamp="")
        asyncio.ensure_future(self.push_frame(frame, direction))

    def _on_agent_audio(self, audio_event: dict[str, Any]) -> None:
        raw = audio_event.get("audio_base_64") or ""
        if not raw:
            return
        pcm = base64.b64decode(raw)
        if self._closing:
            return
        if not self._agent_speaking:
            self._agent_speaking = True
            asyncio.ensure_future(self.push_frame(TTSStartedFrame()))
        frame = TTSAudioRawFrame(
            audio=pcm,
            sample_rate=self._output_sample_rate,
            num_channels=1,
        )
        asyncio.ensure_future(self.push_frame(frame))

    def _end_agent_turn(self) -> None:
        if self._agent_speaking:
            self._agent_speaking = False
            asyncio.ensure_future(self.push_frame(TTSStoppedFrame()))

    def _on_agent_tool_response(self, payload: dict[str, Any]) -> None:
        if payload.get("tool_type") == "system" and payload.get("tool_name") == "end_call":
            logger.info("ElevenLabs: agent called end_call — session will close")

    def _schedule_tool_dispatch(
        self,
        tool_name: str,
        tool_call_id: str,
        params: dict[str, Any],
    ) -> None:
        task = asyncio.create_task(self._dispatch_tool(tool_name, tool_call_id, params))
        self._tool_tasks.add(task)
        task.add_done_callback(self._tool_tasks.discard)

    async def _dispatch_tool(self, tool_name: str, tool_call_id: str, params: dict) -> None:
        started_at = time.monotonic()
        status = "error"
        fn = self._tool_by_name.get(tool_name)
        bridge = _ConvAIParams(self, tool_call_id)
        try:
            if fn is None:
                logger.warning("ElevenLabs: unknown client tool {!r}", tool_name)
                await self.send_tool_result(
                    tool_call_id,
                    {"ok": False, "error": f"unknown tool {tool_name}"},
                    is_error=True,
                )
                status = "unknown"
            else:
                try:
                    await asyncio.wait_for(
                        fn(bridge, **params),
                        timeout=TOOL_DISPATCH_TIMEOUT_SECS,
                    )
                    status = "ok"
                except TimeoutError:
                    status = "timeout"
                    await self.send_tool_result(
                        tool_call_id,
                        {"ok": False, "error": "tool timed out"},
                        is_error=True,
                    )
                except asyncio.CancelledError:
                    status = "cancelled"
                    raise
                except Exception as exc:
                    status = "error"
                    logger.warning("ElevenLabs tool {} failed", tool_name)
                    await self.send_tool_result(
                        tool_call_id, {"ok": False, "error": str(exc)[:500]}, is_error=True
                    )
        finally:
            logger.info(
                "ElevenLabs tool {} completed with status={} latency_ms={:.1f}",
                tool_name,
                status,
                (time.monotonic() - started_at) * 1000,
            )

    async def send_tool_result(self, tool_call_id: str, result: dict, is_error: bool) -> None:
        payload = {
            "type": "client_tool_result",
            "tool_call_id": tool_call_id,
            "result": json.dumps(result, default=str)[:MAX_TOOL_RESULT_CHARS],
            "is_error": is_error,
        }
        if is_error:
            payload["error_type"] = "external_client"
        await self._send_json(payload)

    async def _send_json(self, payload: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            return
        try:
            await ws.send_str(json.dumps(payload, default=str))
        except Exception as exc:
            logger.warning("ElevenLabs WS send failed: {}", exc)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        try:
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                await self._on_input_audio(frame)
            else:
                await self.push_frame(frame, direction)
        finally:
            if isinstance(frame, (CancelFrame, EndFrame, EndTaskFrame)):
                await self.close()

    async def cleanup(self) -> None:
        await self.close()
        await super().cleanup()

    async def _on_input_audio(self, frame: InputAudioRawFrame) -> None:
        ws = self._ws
        if ws is None or ws.closed or not self._opened:
            return
        if frame.sample_rate != 16000:
            logger.warning(
                "ElevenLabs ConvAI expects 16 kHz input, got {} Hz — dropping chunk",
                frame.sample_rate,
            )
            return
        audio = frame.audio
        if frame.num_channels > 1:
            audio = self._to_mono(audio)
        if not audio:
            return
        await self._send_json(
            {"user_audio_chunk": base64.b64encode(audio).decode("ascii")}
        )

    @staticmethod
    def _to_mono(audio: bytes) -> bytes:
        samples = len(audio) // 4
        mono = bytearray(samples * 2)
        for i in range(samples):
            left = int.from_bytes(audio[i * 4 : i * 4 + 2], "little", signed=True)
            right = int.from_bytes(audio[i * 4 + 2 : i * 4 + 4], "little", signed=True)
            mono[i * 2 : i * 2 + 2] = ((left + right) // 2).to_bytes(2, "little", signed=True)
        return bytes(mono)

    async def _cancel_owned_tasks(self, excluded_task: asyncio.Task | None = None) -> None:
        current_task = asyncio.current_task()
        protected_tasks = {current_task, excluded_task}
        tasks = [self._connect_task, self._recv_task, *self._tool_tasks]
        self._tool_tasks.clear()
        pending = [task for task in tasks if task is not None and task not in protected_tasks]
        for task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _close_resources(self) -> None:
        await self._cancel_owned_tasks(self._close_owner)
        ws, agent_id, self._ws, self._agent_id = self._ws, self._agent_id, None, None
        if ws is not None and not ws.closed:
            try:
                await ws.close()
            except Exception as exc:
                logger.debug("ElevenLabs WS close failed: {}", exc)
        if agent_id:
            await delete_agent(self._session, agent_id)
        if not self._session.closed:
            try:
                await self._session.close()
            except Exception as exc:
                logger.debug("ElevenLabs HTTP session close failed: {}", exc)

    async def close(self) -> None:
        current_task = asyncio.current_task()
        if self._close_task is None:
            self._closing = True
            self._opened = False
            self._close_owner = current_task
            self._close_task = asyncio.create_task(self._close_resources())
        if self._close_task is current_task:
            return
        await asyncio.shield(self._close_task)


# ─── Voice cloning CLI ─────────────────────────────────────────────────────────


async def clone_voice(sample_path: str, name: str = "Owner clone") -> str:
    """Instant voice clone: upload a sample, return the new voice_id."""
    path = Path(sample_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    api_key = elevenlabs_api_key()
    data = aiohttp.FormData()
    data.add_field("name", name)
    data.add_field("description", "Owner voice clone for the voicemail agent")
    data.add_field(
        "files",
        path.read_bytes(),
        filename=path.name,
        content_type="audio/m4a" if path.suffix == ".m4a" else "audio/wav",
    )
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{API_BASE}/v1/voices/add", headers={"xi-api-key": api_key}, data=data
        ) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Voice clone failed ({resp.status}): {body[:400]}")
            voice_id = json.loads(body)["voice_id"]
    logger.info("Voice cloned: {} -> {}", sample_path, voice_id)
    return voice_id


def _clone_main(argv: list[str]) -> None:
    sample_path = argv[0] if argv else None
    if not sample_path:
        print("usage: python -m elevenlabs_agent clone <audio-file> [--name \"...\"]")
        sys.exit(1)
    name = "Owner clone"
    if len(argv) > 2 and argv[1] == "--name":
        name = argv[2]
    voice_id = asyncio.run(clone_voice(sample_path, name))
    print(f"voice_id={voice_id}")
    print("Set ELEVENLABS_VOICE_ID=<voice_id> and OWNER_VOICE_CONSENT=true in server/.env")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "clone":
        _clone_main(sys.argv[2:])
    else:
        print("usage: python -m elevenlabs_agent clone <audio-file>")
        sys.exit(1)
