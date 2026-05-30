#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Personal voicemail agent — Nemotron voice path (hackathon build).

The bot answers inbound calls on the owner's behalf, takes a complete message,
and routes it via Twilio SMS and/or Gmail.  Calendar callbacks and caller-persona
enrichment are handled by P2 and P3 modules that append to TOOL_REGISTRY in
server/interfaces.py.

Pipeline: Nemotron STT → Nemotron-3-Super-120B LLM → Gradium (cloned-voice) TTS

Run locally::

    ENV=local uv run bot-nemotron.py
"""

import os

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndTaskFrame, FunctionCallResultProperties, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.types import (
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from interfaces import TOOL_REGISTRY, CallState, build_system_instruction, default_call_state
from nemotron_llm import VLLMOpenAILLMService
from nvidia_stt import NVidiaWebSocketSTTService

# ── P2 / P3 modules append to TOOL_REGISTRY at import time ────────────────────
# Add an import line here once each teammate's module is ready:
#   import calendar_tools   # P2 — appends book_callback_slot, get_calendar_availability
#   import persona_tools    # P3 — appends update_caller_snapshot, lookup_persona

load_dotenv(override=True)


# ─── Twilio helper ────────────────────────────────────────────────────────────


async def get_call_info(call_sid: str) -> dict:
    """Fetch caller number from Twilio REST API."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        logger.warning("Missing Twilio credentials, cannot fetch call info")
        return {}
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=aiohttp.BasicAuth(account_sid, auth_token)) as resp:
                if resp.status != 200:
                    logger.error(f"Twilio API error ({resp.status}): {await resp.text()}")
                    return {}
                data = await resp.json()
                return {"from_number": data.get("from"), "to_number": data.get("to")}
    except Exception as exc:
        logger.error(f"Error fetching Twilio call info: {exc}")
        return {}


# ─── P1 core voicemail tools ──────────────────────────────────────────────────
# These are the baseline tools P1 owns. P2 and P3 append theirs to TOOL_REGISTRY
# from their own modules (see import block above).


async def record_message(
    params: FunctionCallParams,
    call_state: CallState,
    caller_name: str,
    callback_number: str,
    subject: str,
    urgency: str = "normal",
) -> None:
    """Record the caller's complete message into call_state.

    Call this once you have collected the caller's name, callback number,
    subject, and urgency level. Do NOT call it until all four are confirmed.

    Args:
        caller_name: The caller's name as they stated it.
        callback_number: The phone number the caller wants the owner to call back.
        subject: One-sentence summary of why they called.
        urgency: "urgent" | "normal" | "low". Default "normal".
    """
    call_state["voicemail"]["action_items"].append(
        f"[{urgency.upper()}] Call back {caller_name} at {callback_number} re: {subject}"
    )
    logger.info(
        f"Message recorded — caller={caller_name} cb={callback_number} "
        f"urgency={urgency} subject={subject}"
    )
    await params.result_callback(
        {
            "ok": True,
            "recorded": {
                "caller_name": caller_name,
                "callback_number": callback_number,
                "subject": subject,
                "urgency": urgency,
            },
        }
    )


async def notify_owner_sms(
    params: FunctionCallParams,
    call_state: CallState,
    message_body: str,
) -> None:
    """Send a Twilio SMS to the owner summarising the voicemail.

    Call this after record_message has confirmed the message. Use only when
    urgency is "urgent" or when the caller explicitly asks for immediate
    notification. For normal messages, the owner checks their inbox.

    Args:
        message_body: The SMS text to send. Keep it under 160 chars.
            Example: "New voicemail from Alex (+14155551234): urgent — needs
            callback re: contract renewal."
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    owner_number = os.getenv("OWNER_PHONE_NUMBER")
    twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

    if not all([account_sid, auth_token, owner_number, twilio_number]):
        logger.warning("Twilio SMS not configured — skipping owner notification")
        call_state["voicemail"]["sms_sent"] = False
        await params.result_callback({"ok": False, "reason": "SMS not configured"})
        return

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                auth=aiohttp.BasicAuth(account_sid, auth_token),  # type: ignore[arg-type]
                data={"From": twilio_number, "To": owner_number, "Body": message_body},
            ) as resp:
                if resp.status not in (200, 201):
                    logger.error(f"SMS send failed ({resp.status}): {await resp.text()}")
                    await params.result_callback({"ok": False, "reason": "Twilio error"})
                    return
                call_state["voicemail"]["sms_sent"] = True
                logger.info(f"Owner SMS sent: {message_body}")
                await params.result_callback({"ok": True})
    except Exception as exc:
        logger.error(f"SMS error: {exc}")
        await params.result_callback({"ok": False, "reason": str(exc)})


async def end_call(params: FunctionCallParams) -> None:
    """End the call. Only call this AFTER you have said goodbye to the caller
    in the same turn. The pipeline flushes any queued speech, then hangs up."""
    logger.info("end_call — pushing EndTaskFrame upstream")
    await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
    await params.result_callback(
        {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
    )


# Seed TOOL_REGISTRY with P1's tools.
# P2 / P3 append theirs at module import time (see imports above).
TOOL_REGISTRY.extend([record_message, notify_owner_sms, end_call])


# ─── Main bot ─────────────────────────────────────────────────────────────────


async def run_bot(
    transport: BaseTransport,
    from_number: str | None = None,
    audio_in_sample_rate: int = 16000,
    audio_out_sample_rate: int = 24000,
) -> None:
    """Wire up the Pipecat pipeline for one call.

    Args:
        transport: SmallWebRTC (local) or FastAPIWebsocket (Twilio).
        from_number: E.164 caller number from Twilio, or None for WebRTC.
        audio_in_sample_rate: 16 kHz (WebRTC) or 8 kHz (Twilio mulaw).
        audio_out_sample_rate: 24 kHz (WebRTC) or 8 kHz (Twilio mulaw).
    """
    logger.info("Starting voicemail bot")

    # One CallState per call — closed over by all tool closures below.
    call_state = default_call_state(caller_number=from_number or "")

    # ── Bind call_state into tools that need it ───────────────────────────────
    # TOOL_REGISTRY entries that accept a `call_state` parameter get a closure
    # wrapping them so the LLM sees the simpler (no call_state) signature while
    # the implementation still mutates the per-call dict.
    #
    # Convention: if a tool's first non-params arg is named `call_state` and
    # typed CallState, wrap it here. P2/P3 should follow the same pattern in
    # their modules.

    import functools
    import inspect

    def bind_call_state(fn):
        """Wrap a tool whose second param is `call_state` so Pipecat sees the
        correct signature: first param named `params`, no `call_state` param."""
        sig = inspect.signature(fn)
        param_names = list(sig.parameters.keys())
        if len(param_names) > 1 and param_names[1] == "call_state":
            @functools.wraps(fn)
            async def bound(params, **kwargs):
                return await fn(params, call_state=call_state, **kwargs)
            # Drop `call_state` from the visible signature so Pipecat's schema
            # generator doesn't try to inject it as an LLM argument.
            new_params = [p for k, p in sig.parameters.items() if k != "call_state"]
            bound.__signature__ = sig.replace(parameters=new_params)
            return bound
        return fn

    bound_tools = [bind_call_state(fn) for fn in TOOL_REGISTRY]

    # ── P2: inject persona_context before building system instruction ──────────
    # TODO (P2): populate call_state["persona_context"] here by calling your
    #   calendar / iMessage analysis functions, e.g.:
    #       call_state["persona_context"] = await fetch_owner_context(from_number)
    # The build_system_instruction() call below will embed it automatically.

    system_instruction = build_system_instruction(call_state)

    # ── Services ──────────────────────────────────────────────────────────────
    stt = NVidiaWebSocketSTTService(
        url=os.getenv("NVIDIA_ASR_URL", "ws://192.168.7.228:8081"),
        strip_interim_prefix=True,
    )

    enable_thinking = os.getenv("NEMOTRON_ENABLE_THINKING", "false").lower() == "true"
    llm = VLLMOpenAILLMService(
        api_key=os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY"),
        base_url=os.getenv("NEMOTRON_LLM_URL", "http://192.168.7.228:8000/v1"),
        settings=VLLMOpenAILLMService.Settings(
            model=os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super"),
            system_instruction=system_instruction,
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}}},
        ),
    )

    tts = GradiumTTSService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumTTSService.Settings(
            voice=os.getenv("GRADIUM_VOICE_ID", "Eu9iL_CYe8N-Gkx_"),
        ),
    )

    tools = ToolsSchema(standard_tools=bound_tools)
    for fn in bound_tools:
        llm.register_direct_function(fn)

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=FilterIncompleteUserTurnStrategies(),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=audio_in_sample_rate,
            audio_out_sample_rate=audio_out_sample_rate,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        context.add_message(
            {
                "role": "user",
                "content": (
                    "A caller just connected. Greet them warmly and ask how you can help."
                ),
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Client disconnected — call_state summary: {call_state['voicemail']}")
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    await runner.run()


# ─── Entry point ──────────────────────────────────────────────────────────────


async def bot(runner_args: RunnerArguments):
    """Pipecat entry point — routes WebRTC vs Twilio transports."""
    from_number: str | None = None
    transport_overrides: dict = {}

    if os.environ.get("ENV") != "local":
        from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter
        krisp_filter = KrispVivaFilter()
    else:
        krisp_filter = None

    match runner_args:
        case SmallWebRTCRunnerArguments():
            webrtc_connection: SmallWebRTCConnection = runner_args.webrtc_connection
            transport = SmallWebRTCTransport(
                webrtc_connection=webrtc_connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                ),
            )
        case WebSocketRunnerArguments():
            transport_overrides["audio_in_sample_rate"] = 8000
            transport_overrides["audio_out_sample_rate"] = 8000

            _, call_data = await parse_telephony_websocket(runner_args.websocket)
            call_info = await get_call_info(call_data["call_id"])
            if call_info:
                from_number = call_info.get("from_number")
                logger.info(f"Call from {from_number} to {call_info.get('to_number')}")

            serializer = TwilioFrameSerializer(
                stream_sid=call_data["stream_id"],
                call_sid=call_data["call_id"],
                account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            )
            transport = FastAPIWebsocketTransport(
                websocket=runner_args.websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                    add_wav_header=False,
                    serializer=serializer,
                ),
            )
        case _:
            logger.error(f"Unsupported runner type: {type(runner_args)}")
            return

    await run_bot(transport, from_number=from_number, **transport_overrides)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
