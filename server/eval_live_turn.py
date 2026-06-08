"""Live end-to-end turn harness: drives the REAL realtime model with the REAL
system instruction + the REAL check_owner_calendar tool, and prints what the
agent actually says/does + latency. Text-in/text-out (no audio) so it runs here.

Proves: (1) response latency, (2) the agent USES the calendar tool instead of
saying "I don't have access", (3) it speaks in the owner's casual voice.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from dotenv import load_dotenv

load_dotenv("server/.env", override=True) if os.path.exists("server/.env") else None
load_dotenv(".env", override=True)
load_dotenv(".env.local", override=True)

from websockets.asyncio.client import connect  # noqa: E402

import owner_tools  # noqa: E402
from interfaces import build_system_instruction, default_call_state  # noqa: E402
from persona_context import load_persona_context  # noqa: E402

KEY = os.environ["OPENAI_API_KEY"]
MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")

_cs = default_call_state(caller_number="")
_si = build_system_instruction(_cs, caller_name=None, assistant_mode=True)
_persona = load_persona_context()
if _persona:
    _si += "\n\n" + _persona

CAL_TOOL = {
    "type": "function",
    "name": "check_owner_calendar",
    "description": "Check the owner's own calendar — what's scheduled on a day and whether they're free.",
    "parameters": {
        "type": "object",
        "properties": {"date": {"type": "string", "description": "YYYY-MM-DD; default today"}},
    },
}


async def _drain_response(ws):
    """Collect one response: returns (text, fn_name, fn_args, call_id, ms)."""
    t0 = time.perf_counter()
    text = ""
    fn_name = None
    fn_args = ""
    call_id = None
    async for m in ws:
        e = json.loads(m)
        t = e.get("type", "")
        if "output_text.delta" in t or t == "response.text.delta":
            text += e.get("delta", "")
        elif t == "response.function_call_arguments.delta":
            fn_args += e.get("delta", "")
        elif t == "response.function_call_arguments.done":
            fn_name = e.get("name") or fn_name
            fn_args = e.get("arguments", fn_args)
            call_id = e.get("call_id") or call_id
        elif t == "response.output_item.added":
            item = e.get("item", {})
            if item.get("type") == "function_call":
                call_id = item.get("call_id") or call_id
                fn_name = item.get("name") or fn_name
        elif t == "response.done":
            break
        elif t == "error":
            return f"[ERROR] {str(e.get('error'))[:200]}", None, "", None, 0
    return text.strip(), fn_name, fn_args, call_id, (time.perf_counter() - t0) * 1000


async def _send_user(ws, text):
    await ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    }))
    await ws.send(json.dumps({"type": "response.create"}))


async def main():
    url = f"wss://api.openai.com/v1/realtime?model={MODEL}"
    async with connect(url, additional_headers={"Authorization": f"Bearer {KEY}"}) as ws:
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["text"],
                "instructions": _si,
                "tools": [CAL_TOOL],
                "tool_choice": "auto",
                "reasoning": {"effort": os.getenv("OPENAI_REALTIME_REASONING_EFFORT", "minimal")},
            },
        }))

        # Turn 1 — casual greeting (diction + latency; must NOT sound corporate)
        await _send_user(ws, "yo what's good")
        text, fn, args, cid, ms = await _drain_response(ws)
        print(f"\n=== TURN 1: caller says 'yo what's good'  ({ms:.0f} ms) ===")
        print(f"AGENT: {text or '(called tool ' + str(fn) + ')'}")

        # Turn 2 — calendar question (must CALL the tool, not say 'no access')
        await _send_user(ws, "yo am I free this thursday or what")
        text, fn, args, cid, ms = await _drain_response(ws)
        print(f"\n=== TURN 2: caller says 'am I free this thursday'  ({ms:.0f} ms) ===")
        print(f"AGENT calendar tool call -> {fn}  args={args}")
        if fn == "check_owner_calendar" and cid:
            class _P:
                async def result_callback(self, r):
                    self.r = r

            p = _P()
            d = {}
            try:
                d = json.loads(args or "{}")
            except Exception:
                pass
            await owner_tools.check_owner_calendar(p, _cs, date=d.get("date"))
            await ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": cid, "output": json.dumps(p.r)},
            }))
            await ws.send(json.dumps({"type": "response.create"}))
            text2, _, _, _, ms2 = await _drain_response(ws)
            print(f"AGENT (after checking calendar, {ms2:.0f} ms): {text2}")
        elif fn:
            print(f"(model called {fn} — calendar access recognized, not refused)")
        else:
            print(f"AGENT: {text}")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=60))
