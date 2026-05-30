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


# ─── P1 internal helper (not a tool) ─────────────────────────────────────────


async def _send_owner_sms(call_state: CallState, body: str) -> None:
    """Fire-and-forget Twilio SMS to the owner. Called by finish_voicemail."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    owner_number = os.getenv("OWNER_PHONE_NUMBER")
    twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

    if not all([account_sid, auth_token, owner_number, twilio_number]):
        logger.warning("Twilio SMS not configured — skipping owner notification")
        return

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                auth=aiohttp.BasicAuth(account_sid, auth_token),  # type: ignore[arg-type]
                data={"From": twilio_number, "To": owner_number, "Body": body},
            ) as resp:
                if resp.status not in (200, 201):
                    logger.error(f"SMS send failed ({resp.status}): {await resp.text()}")
                    return
                call_state["voicemail"]["sms_sent"] = True
                logger.info(f"Owner SMS sent: {body}")
    except Exception as exc:
        logger.error(f"SMS error: {exc}")


# ─── end_call — module-level (no call_state needed) ──────────────────────────


async def end_call(params: FunctionCallParams) -> None:
    """End the call. Only call this AFTER you have said goodbye to the caller
    in the same turn. The pipeline flushes any queued speech, then hangs up."""
    logger.info("end_call — pushing EndTaskFrame upstream")
    await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
    await params.result_callback(
        {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
    )


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

    # One CallState per call — closed over by all P1 tool closures below.
    call_state = default_call_state(caller_number=from_number or "")

    # ── P1 voicemail tools (closures — close over call_state directly) ────────

    async def capture_caller_identity(params: FunctionCallParams, name: str) -> None:
        """Store the caller's name. Call this as soon as they give their name.

        Args:
            name: The caller's name as they stated it.
        """
        call_state["voicemail"]["message"]["caller_name"] = name
        logger.debug(f"capture_caller_identity: {name!r}")
        await params.result_callback({"ok": True, "caller_name": name})

    async def capture_message_reason(params: FunctionCallParams, reason: str) -> None:
        """Store the reason the caller is leaving a message. Call this once
        they've explained what they're calling about, even briefly.

        Args:
            reason: A concise description of the caller's topic or request.
        """
        call_state["voicemail"]["message"]["reason"] = reason
        logger.debug(f"capture_message_reason: {reason!r}")
        await params.result_callback({"ok": True, "reason": reason})

    async def capture_urgency(params: FunctionCallParams, urgency: str) -> None:
        """Store the caller's urgency level.

        Args:
            urgency: One of "urgent", "normal", or "low".
        """
        level = urgency.lower().strip()
        if level not in ("urgent", "normal", "low"):
            level = "normal"
        call_state["voicemail"]["message"]["urgency"] = level
        logger.debug(f"capture_urgency: {level!r}")
        await params.result_callback({"ok": True, "urgency": level})

    async def capture_callback_preference(
        params: FunctionCallParams,
        wants_callback: bool,
        callback_number: str = "",
        callback_preferred_time: str = "",
    ) -> None:
        """Store the caller's callback preference.

        Args:
            wants_callback: True if the caller wants the owner to call them back.
            callback_number: Phone number to call back. Omit if wants_callback is False.
            callback_preferred_time: When the caller prefers to be called
                (e.g. "after 3 pm", "tomorrow morning"). Omit if not specified.
        """
        msg = call_state["voicemail"]["message"]
        msg["wants_callback"] = wants_callback
        msg["callback_number"] = callback_number
        msg["callback_preferred_time"] = callback_preferred_time
        logger.debug(
            f"capture_callback_preference: wants={wants_callback} "
            f"num={callback_number!r} time={callback_preferred_time!r}"
        )
        await params.result_callback(
            {
                "ok": True,
                "wants_callback": wants_callback,
                "callback_number": callback_number,
                "callback_preferred_time": callback_preferred_time,
            }
        )

    async def get_voicemail_summary(params: FunctionCallParams) -> None:
        """Return the message collected so far so you can read it back to the
        caller for confirmation. Call this at step 5 — before finish_voicemail."""
        msg = call_state["voicemail"]["message"]
        name = msg.get("caller_name", "(not captured)")
        reason = msg.get("reason", "(not captured)")
        urgency = msg.get("urgency", "normal")
        wants_cb = msg.get("wants_callback", False)
        cb_num = msg.get("callback_number", "")
        cb_time = msg.get("callback_preferred_time", "")

        summary_parts = [
            f"Caller: {name}",
            f"Reason: {reason}",
            f"Urgency: {urgency}",
        ]
        if wants_cb:
            cb_line = f"Callback requested at {cb_num}"
            if cb_time:
                cb_line += f", preferred time: {cb_time}"
            summary_parts.append(cb_line)
        else:
            summary_parts.append("No callback requested")

        summary_text = ". ".join(summary_parts) + "."
        logger.debug(f"get_voicemail_summary: {summary_text}")
        await params.result_callback(
            {
                "summary": summary_text,
                "message": dict(msg),
                "instruction": (
                    "Read this summary back to the caller word for word, "
                    "then ask: 'Does that sound right?'"
                ),
            }
        )

    async def finish_voicemail(params: FunctionCallParams) -> None:
        """Persist the voicemail, log the structured summary, and send an SMS
        to the owner if the message is urgent. Call this at step 6, immediately
        before saying goodbye and calling end_call."""
        msg = call_state["voicemail"]["message"]
        name = msg.get("caller_name", "Unknown")
        reason = msg.get("reason", "")
        urgency = msg.get("urgency", "normal")
        wants_cb = msg.get("wants_callback", False)
        cb_num = msg.get("callback_number", "")
        cb_time = msg.get("callback_preferred_time", "")
        caller_number = call_state["voicemail"]["caller_number"]

        # Build human-readable summary
        cb_clause = ""
        if wants_cb and cb_num:
            cb_clause = f" Callback: {cb_num}"
            if cb_time:
                cb_clause += f" ({cb_time})"
            cb_clause += "."

        summary = (
            f"[{urgency.upper()}] Voicemail from {name}"
            + (f" ({caller_number})" if caller_number else "")
            + f": {reason}.{cb_clause}"
        )
        call_state["voicemail"]["summary"] = summary

        action = f"[{urgency.upper()}] Reply to {name}"
        if cb_num:
            action += f" at {cb_num}"
        if cb_time:
            action += f" ({cb_time} preferred)"
        action += f" re: {reason}"
        call_state["voicemail"]["action_items"] = [action]

        logger.info(
            "finish_voicemail | summary=%r | message=%s | sms_sent=%s",
            summary,
            msg,
            call_state["voicemail"]["sms_sent"],
        )

        # Auto-SMS the owner on urgent messages
        if urgency == "urgent":
            sms_body = f"Urgent voicemail: {summary}"[:160]
            await _send_owner_sms(call_state, sms_body)

        await params.result_callback(
            {
                "ok": True,
                "summary": summary,
                "instruction": (
                    "Say a short goodbye now and call end_call in this same turn. "
                    "Example: 'Great, I'll make sure they get your message. Take care!'"
                ),
            }
        )

    # P1's tool list — closures above + module-level end_call
    p1_tools = [
        capture_caller_identity,
        capture_message_reason,
        capture_urgency,
        capture_callback_preference,
        get_voicemail_summary,
        finish_voicemail,
        end_call,
    ]

    # ── Bind call_state into P2/P3 TOOL_REGISTRY entries ─────────────────────
    # Convention: if a registry tool's second param is named `call_state`, it
    # gets wrapped so Pipecat sees `(params, ...)` with no call_state arg.

    import functools
    import inspect

    def bind_call_state(fn):
        sig = inspect.signature(fn)
        param_names = list(sig.parameters.keys())
        if len(param_names) > 1 and param_names[1] == "call_state":
            @functools.wraps(fn)
            async def bound(params, **kwargs):
                return await fn(params, call_state=call_state, **kwargs)
            new_params = [p for k, p in sig.parameters.items() if k != "call_state"]
            bound.__signature__ = sig.replace(parameters=new_params)
            return bound
        return fn

    all_tools = p1_tools + [bind_call_state(fn) for fn in TOOL_REGISTRY]

    # ── P2: inject persona_context before building system instruction ──────────
    # TODO (P2): set call_state["persona_context"] = await fetch_owner_context(from_number)

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

    tools = ToolsSchema(standard_tools=all_tools)
    for fn in all_tools:
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
                "content": "A caller just connected. Begin step 1: greet them and ask for their name.",
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
