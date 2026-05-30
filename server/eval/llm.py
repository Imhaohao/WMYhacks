"""LLM engine abstraction for the eval harness.

Routing (per the repo's AGENTS.md model-routing guidance, "Local Ollama"):

    1. Ollama on compute-box (remote, default)   -- free, private
    2. Ollama on localhost (fallback)            -- free, private
    3. OpenAI (if OPENAI_API_KEY set)            -- reliable quality
    4. Offline deterministic stub                -- always works, for testing

Every call goes through `complete()`. The harness therefore runs end-to-end
even with no network, no Ollama, and no API keys (stub mode) -- the full
simulate -> score -> improve loop is demoable today, and transparently
upgrades to real models the moment they are reachable.

Set CEKURA_EVAL_ENGINE to pin an engine: "ollama" | "openai" | "stub" | "auto"
(default "auto" = try in the order above).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

# --- configuration -------------------------------------------------------

# compute-box first (per AGENTS.md), then localhost.
OLLAMA_HOSTS = [
    h.strip()
    for h in os.getenv(
        "OLLAMA_HOSTS",
        "http://100.85.119.16:11434,http://10.0.0.229:11434,http://localhost:11434",
    ).split(",")
    if h.strip()
]
# Fast-lane default per AGENTS.md; override with OLLAMA_MODEL.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

OPENAI_MODEL = os.getenv("OPENAI_EVAL_MODEL", "gpt-4.1-mini")

ENGINE = os.getenv("CEKURA_EVAL_ENGINE", "auto").lower()

_HTTP_TIMEOUT = float(os.getenv("CEKURA_EVAL_TIMEOUT", "60"))


@dataclass
class LLMResult:
    text: str
    engine: str  # which backend actually answered


def _post_json(
    url: str,
    payload: dict,
    timeout: float = _HTTP_TIMEOUT,
    headers: dict | None = None,
) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# --- backends ------------------------------------------------------------


def _try_ollama(prompt: str, system: str | None, model: str) -> str | None:
    body = {
        "model": model,
        "prompt": prompt,
        "system": system or "",
        "stream": False,
        "options": {"temperature": float(os.getenv("CEKURA_EVAL_TEMP", "0.4"))},
    }
    for host in OLLAMA_HOSTS:
        try:
            out = _post_json(f"{host}/api/generate", body, timeout=_HTTP_TIMEOUT)
            text = (out.get("response") or "").strip()
            if text:
                return text
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            continue
    return None


def _try_openai(prompt: str, system: str | None, model: str) -> str | None:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": model,
        "messages": messages,
        "temperature": float(os.getenv("CEKURA_EVAL_TEMP", "0.4")),
    }
    try:
        out = _post_json(
            "https://api.openai.com/v1/chat/completions",
            body,
            timeout=_HTTP_TIMEOUT,
            headers={"Authorization": f"Bearer {key}"},
        )
        return out["choices"][0]["message"]["content"].strip()
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        KeyError,
        IndexError,
        json.JSONDecodeError,
    ):
        return None


# --- offline deterministic stub -----------------------------------------
#
# The stub lets the whole pipeline run and produce a *believable and
# internally consistent* scorecard with no dependencies. The key property
# for the demo: a WEAK agent prompt must yield a weak transcript that the
# stub judge then scores as failing -- so the auto-improvement loop has real
# room to flip metrics FAIL -> PASS. It plays all four roles, keyed off
# markers we embed in prompts (CALLER_PERSONA, JUDGE_METRIC, IMPROVE_PROMPT;
# anything else = the agent).

# A prompt is "strong" if it actually instructs the agent to screen + capture.
_SCREENING_SIGNALS = (
    "screen",
    "callback",
    "decline",
    "spam",
    "who's calling",
    "who is calling",
    "regarding",
    "in character",
    "priority",
    "do not take a message",
)


def _is_strong_agent_prompt(system: str | None) -> bool:
    s = (system or "").lower()
    return sum(sig in s for sig in _SCREENING_SIGNALS) >= 3


# Markers the stub agent leaves in the transcript so the stub judge can grade
# behavior deterministically.
_WEAK_AGENT_LINE = "Sure, I can pass along a message to them. What would you like me to say?"
_STRONG_AGENT_LINE = (
    "The owner isn't available right now. May I ask who's calling, what it's "
    "regarding, and the best callback number?"
)


def _stub(prompt: str, system: str | None) -> str:
    text = f"{system or ''}\n{prompt}".lower()

    # Role: LLM-as-judge -> grade the TRANSCRIPT against the metric.
    if "judge_metric" in text:
        return _stub_judge(text)

    # Role: prompt-improver -> return an improved system prompt.
    if "improve_prompt" in text:
        return _STUB_IMPROVED_PROMPT

    # Role: simulated caller -> short in-character line.
    if "caller_persona" in text:
        return "Hi, yes -- I'm calling and I'd like to get through, please."

    # Role: voicemail agent -> behavior depends on prompt strength.
    return _STRONG_AGENT_LINE if _is_strong_agent_prompt(system) else _WEAK_AGENT_LINE


def _stub_judge(text: str) -> str:
    """Deterministic transcript grader for offline mode.

    `text` is the lowercased judge system+prompt, which contains the metric id,
    the expected outcome, and the full transcript. We grade by inspecting what
    the agent actually did.
    """
    strong = _STRONG_AGENT_LINE.lower()[:40] in text  # agent screened/asked
    expected_block = "expected outcome for this caller: block" in text

    def verdict(score: float, passed: bool, why: str) -> str:
        return json.dumps({"score": score, "passed": passed, "reasoning": why})

    if "id=correct_screening" in text:
        if strong:
            return verdict(0.9, True, "Stub: agent screened and acted correctly.")
        if expected_block:
            return verdict(
                0.3,
                False,
                "Stub: agent took a message for a caller who should be blocked.",
            )
        return verdict(0.6, False, "Stub: weak screening, no clear classification.")

    if "id=message_captured" in text:
        if expected_block:
            return verdict(1.0, True, "Stub: block case, no message needed (N/A).")
        if strong:
            return verdict(0.9, True, "Stub: captured who/what/callback.")
        return verdict(0.35, False, "Stub: no callback number captured, message incomplete.")

    # Other metrics are satisfied by either behavior in the stub world.
    return verdict(0.85, True, "Stub: behavior meets the metric criteria.")


_STUB_IMPROVED_PROMPT = """You are the personal voicemail assistant for the owner of this phone.
The owner is unavailable. Your job is to SCREEN every inbound caller and take
a clean message.

Always do all of the following:
1. Politely greet and state the owner is unavailable.
2. Ask WHO is calling (name + who they're with).
3. Ask WHAT the call is regarding.
4. Ask for the BEST CALLBACK NUMBER and read it back to confirm.
5. Decide: spam/sales -> politely decline and do NOT take a message;
   legitimate -> capture a complete message (who / what / callback).
6. For persistent salespeople: stay polite, decline twice, then end the call.
7. Treat every caller with respect, even spammers.

Keep replies to 1-2 short sentences, natural for voice. Stay in character as
the owner's assistant at all times -- never reveal you are an AI model."""


# --- public API ----------------------------------------------------------


def complete(prompt: str, system: str | None = None, model: str | None = None) -> LLMResult:
    """Return a completion, trying engines in priority order.

    Never raises on backend failure -- always falls back to the stub so the
    harness keeps running.
    """
    order = ["ollama", "openai", "stub"] if ENGINE == "auto" else [ENGINE]
    for eng in order:
        if eng == "ollama":
            out = _try_ollama(prompt, system, model or OLLAMA_MODEL)
            if out is not None:
                return LLMResult(out, "ollama")
        elif eng == "openai":
            out = _try_openai(prompt, system, model or OPENAI_MODEL)
            if out is not None:
                return LLMResult(out, "openai")
        elif eng == "stub":
            return LLMResult(_stub(prompt, system), "stub")
    # If a pinned non-stub engine failed, still degrade gracefully.
    return LLMResult(_stub(prompt, system), "stub")


def active_engine() -> str:
    """Probe which engine would currently answer (for status/demo banners)."""
    probe = complete("JUDGE_METRIC ping", system="reply with JSON")
    return probe.engine


if __name__ == "__main__":
    r = complete("Say hello in 5 words.")
    print(f"[engine={r.engine}] {r.text}")
