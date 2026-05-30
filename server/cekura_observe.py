"""Single Cekura observability network client for the running bot.

WHY THIS EXISTS
---------------
The Cekura eval harness is MCP-driven (Claude Code calls MCP tools to provision
evals and read results).  A running Pipecat bot cannot use MCP tools at
runtime — it has no connection to the MCP server.  Observability therefore
needs a direct HTTP POST from inside the bot process: one call per completed
call, fired and forgotten before the pipeline tears down.

This module is the ONLY file in the server that makes network calls to Cekura
from Python.  All other Cekura interactions go through the MCP server in the
dev session (Claude Code tooling), not through bot code.

ENDPOINT CHOSEN
---------------
``POST /observability/v1/observe/`` — the generic "Custom Integration" ingestion
endpoint documented in cekura-skills/cekura/skills/cekura-create-agent/references/integrations.md.
This is the correct path for self-hosted / custom agents (our agent is registered as
assistant_provider=self_hosted).  Provider-specific webhooks (e.g. /pipecat/observe/)
exist for Pipecat Cloud auto-fetch, but those expect a provider-side webhook post-call
event — not a direct bot-side POST.  Override via CEKURA_OBSERVE_URL if the path
ever changes.

GATE
----
Set ``CEKURA_OBSERVE=1`` (or ``true``) in the environment to enable actual POSTs.
Unset (or any other value) → this module is a silent no-op so local dev stays
fully offline.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

# ─── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_BASE_URL = "https://api.cekura.ai"
_DEFAULT_OBSERVE_PATH = "/observability/v1/observe/"

_IDS_FILE = Path(__file__).resolve().parent / "eval" / "cekura_ids.json"

# ─── Agent-id resolution ──────────────────────────────────────────────────────


def _resolve_agent_id(passed_id: int | None) -> int | None:
    """Return the agent id from env → cekura_ids.json → passed argument."""
    env_val = os.getenv("CEKURA_AGENT_ID")
    if env_val:
        try:
            return int(env_val)
        except ValueError:
            logger.warning(f"cekura_observe: CEKURA_AGENT_ID={env_val!r} is not an integer")

    try:
        data = json.loads(_IDS_FILE.read_text())
        return int(data["agent_id"])
    except Exception:
        pass

    return passed_id


# ─── Transcript helpers ───────────────────────────────────────────────────────


def _turns_from_context_messages(messages: list[dict]) -> list[dict]:
    """Convert LLMContext message list to Cekura 'cekura' transcript format.

    Roles:
        "assistant" → "Main Agent"   (our bot)
        "user"      → "Testing Agent" (the real human caller in production)
        other roles (system, tool, function) → skip

    start_time / end_time are not tracked per-turn at runtime so we emit 0.0.
    """
    turns: list[dict] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content")

        if role == "assistant":
            cekura_role = "Main Agent"
        elif role == "user":
            cekura_role = "Testing Agent"
        else:
            # Skip system prompts, tool calls, function results
            continue

        # Content can be a string or a list of content blocks (OpenAI style)
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            text = " ".join(parts).strip()
        else:
            text = str(content).strip() if content else ""

        if not text:
            continue

        turns.append(
            {
                "role": cekura_role,
                "content": text,
                "start_time": 0.0,
                "end_time": 0.0,
            }
        )
    return turns


def _turns_from_string_transcript(raw: str) -> list[dict]:
    """Parse a plain-string running transcript into Cekura turns.

    Falls back to wrapping the whole string as a single Main Agent turn if no
    structured lines are found.  This is only used when context messages are
    unavailable (bot-gpt.py metadata-only path).
    """
    if not raw or not raw.strip():
        return []

    # Try to detect "Role: text" line format if the transcript uses it
    line_re = re.compile(r"^(assistant|user|bot|caller):\s*(.+)$", re.IGNORECASE)
    turns: list[dict] = []
    for line in raw.splitlines():
        m = line_re.match(line.strip())
        if m:
            raw_role = m.group(1).lower()
            text = m.group(2).strip()
            cekura_role = "Testing Agent" if raw_role in ("user", "caller") else "Main Agent"
            turns.append({"role": cekura_role, "content": text, "start_time": 0.0, "end_time": 0.0})
    if turns:
        return turns

    # No parseable structure — return empty (metadata-only)
    return []


# ─── Main public function ─────────────────────────────────────────────────────


async def observe_call(
    call_state: dict,
    *,
    call_id: str,
    agent_id: int | None = None,
    customer_number: str | None = None,
    call_ended_reason: str = "completed",
    extra_metadata: dict[str, Any] | None = None,
    transcript_turns: list[dict] | None = None,
) -> None:
    """POST a completed call to Cekura Observability.  Never raises.

    Parameters
    ----------
    call_state:
        The bot's call_state dict (CallState).  Metadata is read from
        call_state["voicemail"].
    call_id:
        Stable unique id for this call (Twilio call SID, or a derived string).
    agent_id:
        Override the Cekura agent id.  Resolution order: CEKURA_AGENT_ID env →
        eval/cekura_ids.json → this argument.
    customer_number:
        E.164 caller number.  Defaults to call_state["voicemail"]["caller_number"].
    call_ended_reason:
        Cekura-style end reason (e.g. "completed", "voicemail", "no-answer").
    extra_metadata:
        Arbitrary dict merged into the ``metadata`` body field.
    transcript_turns:
        Pre-built cekura-format turns.  If provided, used directly; otherwise
        the voicemail string transcript is parsed (may yield an empty list).
    """
    # ── Gate: only POST when opt-in flag is set ────────────────────────────────
    observe_enabled = os.getenv("CEKURA_OBSERVE", "").lower() in ("1", "true", "yes")
    if not observe_enabled:
        logger.debug("cekura_observe: CEKURA_OBSERVE not set — skipping observability POST")
        return

    api_key = os.getenv("CEKURA_API_KEY", "")
    if not api_key:
        logger.debug("cekura_observe: CEKURA_API_KEY not set — skipping observability POST")
        return

    resolved_agent_id = _resolve_agent_id(agent_id)
    if resolved_agent_id is None:
        logger.warning("cekura_observe: no agent_id available — skipping observability POST")
        return

    # ── Build transcript ───────────────────────────────────────────────────────
    if transcript_turns is not None:
        turns = transcript_turns
    else:
        raw_transcript: str = (call_state.get("voicemail") or {}).get("transcript", "")
        turns = _turns_from_string_transcript(raw_transcript)

    # ── Build body ─────────────────────────────────────────────────────────────
    voicemail: dict = call_state.get("voicemail") or {}
    caller_num = customer_number or voicemail.get("caller_number") or ""
    recorded_at: str = voicemail.get("recorded_at") or datetime.now(UTC).isoformat()
    duration: int = voicemail.get("duration_seconds") or 0

    metadata: dict[str, Any] = {
        "recorded_at": recorded_at,
        "duration_seconds": duration,
        "summary": voicemail.get("summary") or "",
        "urgency": (voicemail.get("message") or {}).get("urgency") or "",
        "sms_sent": voicemail.get("sms_sent") or False,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    body: dict[str, Any] = {
        "call_id": call_id,
        "agent": resolved_agent_id,
        "transcript_type": "cekura",
        "transcript_json": turns,
        "call_ended_reason": call_ended_reason,
        "timestamp": datetime.now(UTC).isoformat(),
        "metadata": metadata,
    }
    if caller_num:
        body["customer_number"] = caller_num

    # ── Determine URL ──────────────────────────────────────────────────────────
    observe_url = os.getenv("CEKURA_OBSERVE_URL", "")
    if not observe_url:
        base = os.getenv("CEKURA_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
        observe_url = base + _DEFAULT_OBSERVE_PATH

    # ── Fire and forget ────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                observe_url,
                json=body,
                headers={"X-CEKURA-API-KEY": api_key},
            )
        if resp.status_code in (200, 201, 202):
            logger.info(
                f"cekura_observe: ingested call {call_id!r} → "
                f"agent={resolved_agent_id} turns={len(turns)} status={resp.status_code}"
            )
        else:
            logger.warning(
                f"cekura_observe: POST failed status={resp.status_code} "
                f"call_id={call_id!r} body_preview={resp.text[:200]!r}"
            )
    except Exception as exc:
        logger.warning(f"cekura_observe: POST exception for call {call_id!r} — {exc}")
