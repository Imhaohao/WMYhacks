"""Run one screened call locally, as text, through the bot's real call logic.

Uses bot-nemotron.py's tenant routing, system prompt and tool functions, with a
local Ollama model standing in for the voice LLM and a scripted caller
standing in for speech. No audio, Twilio, ElevenLabs
or AWS: those are disabled below, so SMS is skipped and records stay local.

    uv run python ops/local_text_call.py --store /tmp/gotchu --forwarded-from +14155550101 \
        --to +16505550188 --caller-number +14155550143 --script caller.txt
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

SERVER_DIR = Path(__file__).resolve().parent.parent
DISABLED_ENV = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "ELEVENLABS_API_KEY", "RESEND_API_KEY")
MAX_AGENT_STEPS = 40
CONTEXT_TOKENS = 16384  # the prompt plus 18 tool schemas is ~5k tokens; Ollama defaults to 4k


def _isolate_environment(store: Path, model_url: str, model: str) -> None:
    for name in DISABLED_ENV:
        os.environ.pop(name, None)
    os.environ.update(
        {
            "VOICE_LLM_PROVIDER": "nemotron",
            "NEMOTRON_LLM_URL": model_url,
            "NEMOTRON_LLM_MODEL": model,
            "TENANT_STORE_FORCE_LOCAL": "true",
            "TENANT_STORE_LOCAL_PATH": str(store / "tenants.json"),
            "PERSIST_LOCAL_PATH": str(store / "records.jsonl"),
            "PERSIST_OUTBOX_PATH": str(store / "outbox.jsonl"),
            "AWS_SHARED_CREDENTIALS_FILE": "/dev/null",
            "AWS_CONFIG_FILE": "/dev/null",
        }
    )
    os.environ.pop("AWS_PROFILE", None)


def _load_bot() -> Any:
    sys.path.insert(0, str(SERVER_DIR))
    spec = importlib.util.spec_from_file_location("gotchu_bot", SERVER_DIR / "bot-nemotron.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _SilentLLM:
    async def push_frame(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _ToolParams:
    """The slice of Pipecat's FunctionCallParams the bot's tools use."""

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.function_name = name
        self.arguments = arguments
        self.llm = _SilentLLM()
        self.result: Any = None

    async def result_callback(self, result: Any, properties: Any = None) -> None:
        self.result = result


async def _run_tool(tools: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
    name = call["function"]["name"]
    arguments = call["function"].get("arguments") or {}
    params = _ToolParams(name, arguments)
    await tools[name](params, **arguments)
    print(f"  [tool] {name}({arguments}) -> {params.result}")
    return {"role": "tool", "tool_name": name, "content": json.dumps(params.result, default=str)}


async def _chat(session: Any, args: argparse.Namespace, messages: list, schema: list) -> dict:
    payload = {
        "model": args.model,
        "messages": messages,
        "tools": schema,
        "stream": False,
        "options": {"num_ctx": CONTEXT_TOKENS, "temperature": 0.3},
    }
    async with session.post(f"{args.ollama_url}/api/chat", json=payload) as resp:
        resp.raise_for_status()
        return (await resp.json())["message"]


async def _agent_turn(session: Any, args: argparse.Namespace, messages: list, schema: list, tools: dict) -> bool:
    """Let the agent speak (and call tools) until it waits for the caller."""
    for _ in range(MAX_AGENT_STEPS):
        message = await _chat(session, args, messages, schema)
        calls = message.get("tool_calls") or []
        messages.append({"role": "assistant", "content": message.get("content", ""), "tool_calls": calls})
        if message.get("content"):
            print(f"Gotchu: {message['content']}")
        if not calls:
            return False
        for call in calls:
            messages.append(await _run_tool(tools, call))
            if call["function"]["name"] == "end_call":
                return True
    return True


async def run_call(args: argparse.Namespace) -> None:
    import aiohttp
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from pipecat.adapters.services.open_ai_adapter import OpenAILLMAdapter

    bot = _load_bot()
    call_state = bot.default_call_state(caller_number=args.caller_number)
    call_state["tenant_runtime"] = bot._tenant_runtime_for_call(
        args.to, forwarded_from=args.forwarded_from
    )
    if not call_state["tenant_runtime"]:
        raise SystemExit("No tenant matched --to/--forwarded-from in the local store.")
    hangup = {"worker": None, "finalized": False, "ending": False}
    all_tools = bot._build_all_tools(call_state, hangup, {"task": None}, False, False)
    from persona_context import build_call_persona_context

    call_state["persona_context"] = build_call_persona_context(call_state["tenant_runtime"])
    caller_name = bot.persona_tools.greeting_for_caller(call_state)
    system = bot.build_system_instruction(call_state, caller_name=caller_name, assistant_mode=False)
    schema = OpenAILLMAdapter().to_provider_tools_format(ToolsSchema(standard_tools=all_tools))
    tools = {fn.__name__: fn for fn in all_tools}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": bot._caller_connected_opener(caller_name)},
    ]
    script = [line.strip() for line in Path(args.script).read_text().splitlines() if line.strip()]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
        ended = await _agent_turn(session, args, messages, schema, tools)
        for line in script:
            if ended:
                break
            print(f"Caller: {line}")
            bot.persona_tools.observe_transcript_frame(call_state, type("Frame", (), {"text": line}))
            messages.append({"role": "user", "content": line})
            ended = await _agent_turn(session, args, messages, schema, tools)
    await bot._finalize_call(call_state, hangup, "local_text_call")
    await asyncio.sleep(0.5)
    print(json.dumps(call_state["voicemail"]["message"], indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--to", required=True, help="The Gotchu line the call reached")
    parser.add_argument("--forwarded-from", required=True, help="The owner's mobile number")
    parser.add_argument("--caller-number", required=True)
    parser.add_argument("--script", required=True, help="One caller line per row")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--model", default="qwen2.5:7b")
    args = parser.parse_args()
    _isolate_environment(args.store, f"{args.ollama_url}/v1", args.model)
    asyncio.run(run_call(args))


if __name__ == "__main__":
    main()
