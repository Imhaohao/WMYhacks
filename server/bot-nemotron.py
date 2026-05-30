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

Pipeline: Gradium STT → Nemotron-3-Super-120B LLM → Gradium (cloned-voice) TTS

Run locally::

    ENV=local uv run bot-nemotron.py
"""

import asyncio
import json
import os
from pathlib import Path
import time

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

_ENV_DIR = Path(__file__).resolve().parent
load_dotenv(_ENV_DIR / ".env", override=True)
load_dotenv(_ENV_DIR / ".env.local", override=True)
from pipecat.services.gradium.stt import GradiumSTTService
from pipecat.transcriptions.language import Language

from interfaces import TOOL_REGISTRY, CallState, build_system_instruction, default_call_state
from nemotron_llm import VLLMOpenAILLMService

# ── P2 / P3 modules append to TOOL_REGISTRY at import time ────────────────────
# Add an import line here once each teammate's module is ready:
#   import calendar_tools   # P2 — appends book_callback_slot, get_calendar_availability
from interfaces import TOOL_REGISTRY, CallState, build_system_instruction, default_call_state
from nemotron_llm import VLLMOpenAILLMService
from nvidia_stt import NVidiaWebSocketSTTService

import persona_tools  # isort: skip  # P3 — appends 4 tools and on_call_finished hook


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


# ─── Voice ID resolution — owner clone with consent guard ────────────────────


def _resolve_voice_id() -> str:
    """Return the voice ID to use for TTS.

    Priority:
      1. Owner's cloned voice — requires BOTH:
           GRADIUM_CLONED_VOICE_ID  set to the ID obtained from Gradium's
                                    voice-cloning page (gradium.ai → Voices)
           OWNER_VOICE_CONSENT=true  explicit opt-in flag; protects against
                                     accidental or unauthorised cloning use
      2. Default Gradium voice — GRADIUM_VOICE_ID (falls back to the
         hardcoded default if that env var is also unset)

    The function logs exactly which path was taken so it's always auditable.
    NEVER set GRADIUM_CLONED_VOICE_ID to a caller's voice — only the owner
    records the sample and provides consent.
    """
    cloned_id = os.getenv("GRADIUM_CLONED_VOICE_ID", "").strip()
    consent = os.getenv("OWNER_VOICE_CONSENT", "false").strip().lower() == "true"

    if cloned_id and consent:
        logger.info("TTS: using owner's cloned voice (id=%s)", cloned_id)
        return cloned_id

    if cloned_id and not consent:
        logger.warning(
            "TTS: GRADIUM_CLONED_VOICE_ID is set but OWNER_VOICE_CONSENT != true — "
            "falling back to default voice. Set OWNER_VOICE_CONSENT=true to enable."
        )

    default_id = os.getenv("GRADIUM_VOICE_ID", "Eu9iL_CYe8N-Gkx_")
    logger.info("TTS: using default Gradium voice (id=%s)", default_id)
    return default_id


# ─── Triage oracle — thinking-enabled urgency assessment ─────────────────────


async def _triage_with_thinking(
    reason: str,
    caller_name: str,
    stated_urgency: str,
    persona_context: str,
) -> dict:
    """Call Nemotron with thinking ON to independently assess urgency.

    Launched as a background asyncio task from capture_message_reason and
    awaited (with timeout) inside capture_urgency, so the inference latency
    is hidden behind the LLM's question turn + caller's response time.

    Returns a dict:
        urgency          — "urgent" | "normal" | "low"
        confidence       — 0.0–1.0
        one_line_reason  — brief justification string
        reasoning_trace  — full <think> block for the judge trace
    """
    base_url = os.getenv("NEMOTRON_LLM_URL", "http://192.168.7.228:8000/v1").rstrip("/")
    model = os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super")
    api_key = os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY")

    system_msg = (
        "You are a message-triage assistant. Assess the TRUE urgency of this voicemail "
        "based on its content — not just what the caller claims.\n\n"
        "Urgency levels:\n"
        "- urgent: Same-day or immediate attention. Time-sensitive decision, safety concern, "
        "VIP or priority contact, or a missed opportunity if delayed.\n"
        "- normal: Standard matter. Can be handled within 1–2 business days.\n"
        "- low: No time pressure. Informational or low-stakes.\n"
        + (f"\nOwner context:\n{persona_context}\n" if persona_context else "")
        + "\nRespond with EXACTLY this JSON object and no other text:\n"
        '{"urgency": "urgent"|"normal"|"low", "confidence": 0.0-1.0, "one_line_reason": "..."}'
    )
    user_msg = (
        f"Caller name: {caller_name or 'Unknown'}.\n"
        f"Message: {reason}.\n"
        + (f"Caller's stated urgency: {stated_urgency}." if stated_urgency else "")
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.1,
        "max_tokens": 512,
        "stream": False,
        "extra_body": {"chat_template_kwargs": {"enable_thinking": True}},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"{base_url}/chat/completions"
    fallback = {
        "urgency": stated_urgency or "normal",
        "confidence": 0.0,
        "one_line_reason": "triage_unavailable",
        "reasoning_trace": "",
    }

    t0 = time.monotonic()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning("Triage HTTP %s: %s", resp.status, (await resp.text())[:200])
                    return fallback
                data = await resp.json()

        elapsed = time.monotonic() - t0
        choice_msg = data["choices"][0]["message"]

        # Extract reasoning trace.
        # Path A — vLLM reasoning parser active: separate "reasoning_content" field.
        # Path B — no parser: trace is inline wrapped in <think>…</think> tags.
        reasoning_trace: str = choice_msg.get("reasoning_content") or ""
        content: str = choice_msg.get("content") or ""

        if not reasoning_trace and "<think>" in content and "</think>" in content:
            t_start = content.index("<think>") + len("<think>")
            t_end = content.index("</think>")
            reasoning_trace = content[t_start:t_end].strip()
            content = content[t_end + len("</think>"):].strip()

        # Parse the JSON decision; fall back to keyword scan if malformed.
        try:
            decision = json.loads(content.strip())
        except (json.JSONDecodeError, ValueError):
            lower = content.lower()
            decision = {
                "urgency": "urgent" if "urgent" in lower else ("low" if "low" in lower else "normal"),
                "confidence": 0.4,
                "one_line_reason": content[:100].strip(),
            }

        model_urgency = decision.get("urgency", "normal")
        confidence = float(decision.get("confidence", 0.5))
        one_line = decision.get("one_line_reason", "")

        logger.info(
            "TRIAGE | caller=%r reason=%r stated=%r → model=%r conf=%.2f latency=%.1fs | %s",
            caller_name, reason[:80], stated_urgency, model_urgency, confidence, elapsed, one_line,
        )
        if reasoning_trace:
            # Clearly delimited so judges can copy the full trace from logs.
            logger.info(
                "TRIAGE REASONING TRACE ── begin ──────────────────────────────\n"
                "%s\n"
                "TRIAGE REASONING TRACE ── end ────────────────────────────────",
                reasoning_trace,
            )

        return {
            "urgency": model_urgency,
            "confidence": confidence,
            "one_line_reason": one_line,
            "reasoning_trace": reasoning_trace,
        }

    except asyncio.CancelledError:
        raise  # let asyncio manage task cancellation normally
    except Exception as exc:
        logger.warning("Triage oracle failed: %s", exc)
        return {**fallback, "one_line_reason": f"error: {exc}"}


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

    # Thinking mode: controls the triage oracle only. Pipeline LLM is always off.
    enable_thinking = os.getenv("NEMOTRON_ENABLE_THINKING", "false").lower() == "true"
    if enable_thinking:
        logger.info("Thinking mode enabled — triage oracle will use Nemotron reasoning")

    # Mutable container for the background triage task; avoids nonlocal boilerplate.
    _triage: dict = {"task": None}

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
        logger.debug("capture_message_reason: %r", reason)

        if enable_thinking:
            # Launch the triage oracle in the background. By the time the LLM
            # asks "how urgent is this?" and the caller replies, the reasoning
            # call will likely be done and capture_urgency can just await it.
            _triage["task"] = asyncio.create_task(
                _triage_with_thinking(
                    reason=reason,
                    caller_name=call_state["voicemail"]["message"].get("caller_name", ""),
                    stated_urgency="",
                    persona_context=call_state["persona_context"],
                )
            )
            logger.debug("Triage oracle launched in background")

        await params.result_callback({"ok": True, "reason": reason})

    async def capture_urgency(params: FunctionCallParams, urgency: str) -> None:
        """Store the caller's urgency level, cross-checked against the reasoning
        triage oracle when NEMOTRON_ENABLE_THINKING is set. The model never
        downgrades what the caller stated — it can only escalate.

        Args:
            urgency: One of "urgent", "normal", or "low".
        """
        _LEVELS = {"urgent": 2, "normal": 1, "low": 0}

        stated = urgency.lower().strip()
        if stated not in _LEVELS:
            stated = "normal"

        final_urgency = stated
        triage_meta: dict = {}

        if _triage["task"] is not None:
            try:
                # asyncio.shield keeps the task alive if wait_for times out,
                # so the trace still gets logged even when we fall back.
                triage = await asyncio.wait_for(
                    asyncio.shield(_triage["task"]), timeout=8.0
                )
                _triage["task"] = None
                triage_meta = triage
                model_urgency = triage["urgency"]

                if _LEVELS.get(model_urgency, 1) > _LEVELS.get(stated, 1):
                    final_urgency = model_urgency
                    logger.info(
                        "Urgency escalated by triage: caller said %r → model assessed %r "
                        "(conf=%.2f, reason: %s)",
                        stated, model_urgency,
                        triage.get("confidence", 0.0),
                        triage.get("one_line_reason", ""),
                    )
                else:
                    logger.info(
                        "Urgency confirmed: caller=%r model=%r (conf=%.2f) → using %r",
                        stated, model_urgency,
                        triage.get("confidence", 0.0),
                        final_urgency,
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    "Triage oracle timed out — using caller's stated urgency: %r", stated
                )
                triage_meta = {
                    "urgency": stated, "confidence": 0.0,
                    "one_line_reason": "timeout", "reasoning_trace": "",
                }

        call_state["voicemail"]["message"]["urgency"] = final_urgency
        call_state["voicemail"]["message"]["triage"] = triage_meta

        logger.debug("capture_urgency: stated=%r final=%r", stated, final_urgency)
        await params.result_callback({
            "ok": True,
            "urgency": final_urgency,
            "stated_urgency": stated,
            **({"triage": {
                "model_urgency": triage_meta.get("urgency", ""),
                "confidence": triage_meta.get("confidence", 0.0),
            }} if triage_meta else {}),
        })

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

    async def notify_owner_sms(params: FunctionCallParams, note: str = "") -> None:
        """Text the owner a one-line callback summary RIGHT NOW via Twilio SMS.

        Call this when the caller explicitly asks to notify the owner immediately
        (e.g. "can you let them know straight away?") or when you judge the
        situation warrants it regardless of whether the full message is complete.

        Safety rules (enforced in code — not just the prompt):
        - SMS goes ONLY to the owner's registered OWNER_PHONE_NUMBER.
        - The caller's number is never used as a destination.
        - Duplicate sends within the same call are silently skipped.

        Args:
            note: Optional extra context to append, max ~30 words.
                  Examples: "caller is waiting on-site",
                             "caller sounded very distressed".
                  Omit for a plain auto-built summary.
        """
        # Idempotency: never double-send within the same call.
        if call_state["voicemail"]["sms_sent"]:
            logger.info("notify_owner_sms: SMS already sent this call — skipping")
            await params.result_callback({
                "ok": True,
                "skipped": True,
                "reason": "SMS already sent for this call",
            })
            return

        msg = call_state["voicemail"]["message"]
        name = msg.get("caller_name") or "Unknown caller"
        reason = msg.get("reason") or "(reason not yet captured)"
        urgency = (msg.get("urgency") or "normal").upper()
        cb_num = msg.get("callback_number") or ""
        caller_number = call_state["voicemail"]["caller_number"]

        # Build the one-line summary: [URGENCY] Name (number) re: reason — cb: num | note
        parts = [f"[{urgency}]", name]
        if caller_number:
            parts.append(f"({caller_number})")
        parts.append(f"re: {reason}")
        if cb_num:
            parts.append(f"— cb: {cb_num}")
        if note:
            parts.append(f"| {note.strip()}")
        body = " ".join(parts)[:160]  # hard cap at Twilio's single-SMS limit

        await _send_owner_sms(call_state, body)

        if call_state["voicemail"]["sms_sent"]:
            logger.info("notify_owner_sms: sent %r", body)
            await params.result_callback({"ok": True, "body": body})
        else:
            await params.result_callback({
                "ok": False,
                "reason": "SMS send failed — check TWILIO_* and OWNER_PHONE_NUMBER in .env",
            })

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

        triage_meta = msg.get("triage", {})
        logger.info(
            "finish_voicemail | summary=%r | urgency=%s | triage_model=%s "
            "triage_conf=%.2f | sms_sent=%s",
            summary,
            urgency,
            triage_meta.get("urgency", "n/a"),
            triage_meta.get("confidence", 0.0),
            call_state["voicemail"]["sms_sent"],
        )
        # Full message payload (excluding trace to avoid log spam on repeat reads)
        logger.debug(
            "finish_voicemail message payload: %s",
            {k: v for k, v in msg.items() if k not in ("triage",)},
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
        notify_owner_sms,
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
            setattr(bound, "__signature__", sig.replace(parameters=new_params))
            return bound
        return fn

    all_tools = p1_tools + [bind_call_state(fn) for fn in TOOL_REGISTRY]

    # ── P2: inject persona_context before building system instruction ──────────
    from owner_config import build_persona_context

    call_state["persona_context"] = build_persona_context()
    # TODO (P2): merge calendar / iMessage analysis on top of owner_config baseline

    system_instruction = build_system_instruction(call_state)

    # ── Services ──────────────────────────────────────────────────────────────
    stt = GradiumSTTService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumSTTService.Settings(
            language=Language.EN,
        ),
    )

    llm = VLLMOpenAILLMService(
        api_key=os.getenv("NEMOTRON_LLM_API_KEY", "EMPTY"),
        base_url=os.getenv("NEMOTRON_LLM_URL", "http://192.168.7.228:8000/v1"),
        settings=VLLMOpenAILLMService.Settings(
            model=os.getenv("NEMOTRON_LLM_MODEL", "nvidia/nemotron-3-super"),
            system_instruction=system_instruction,
            # Pipeline LLM: thinking always OFF. Reasoning tokens can bleed into
            # TTS if the vLLM server has no --reasoning-parser configured.
            # Thinking is used only by the background triage oracle.
            extra={"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
        ),
    )

    tts = GradiumTTSService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumTTSService.Settings(
            voice=_resolve_voice_id(),
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

    # P3 — passthrough processor that infers caller tone from final transcripts
    # (no LLM round-trip). Sits between stt and the user aggregator.
    tone_listener = persona_tools.make_transcript_tone_processor(call_state)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            tone_listener,
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
        persona_tools.on_call_finished(call_state)  # P3 — persist voicemail (never raises)
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
