"""Strict-local summarizer for Part B ingestion.

This is deliberately NOT `eval/llm.py`: that engine falls back to OpenAI, which
would send sensitive content (iMessages) to the cloud. Here the contract is:

- `strict_local=True` (the default): only a local Ollama model is ever
  contacted. If none is reachable, we fall back to the caller's deterministic
  bullets — no network, no cloud, always produces output.
- `strict_local=False`: a cloud model (OpenAI) may be used as a middle tier,
  for NON-sensitive sources only (calendar, agent-context) when the caller opts
  in. iMessage always passes strict_local=True.

The model only ever *polishes* facts the source already extracted locally, so
even with no model the result is sensible — the deterministic fallback bullets.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

# localhost first for ingest (the user runs a local model); compute-box next.
OLLAMA_HOSTS = [
    h.strip()
    for h in os.getenv(
        "OLLAMA_HOSTS",
        "http://localhost:11434,http://100.85.119.16:11434,http://10.0.0.229:11434",
    ).split(",")
    if h.strip()
]
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
OPENAI_MODEL = os.getenv("OPENAI_EVAL_MODEL", "gpt-4.1-mini")
_HTTP_TIMEOUT = float(os.getenv("INGEST_LLM_TIMEOUT", "60"))
_TEMP = float(os.getenv("INGEST_LLM_TEMP", "0.3"))


@dataclass
class SummaryResult:
    lines: list[str]  # bullet lines, each starting with "- "
    engine: str  # "ollama" | "openai" | "deterministic"


def _post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _try_ollama(prompt: str, system: str, model: str) -> str | None:
    body = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": _TEMP},
    }
    for host in OLLAMA_HOSTS:
        try:
            out = _post_json(f"{host}/api/generate", body)
            text = (out.get("response") or "").strip()
            if text:
                return text
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            continue
    return None


def _try_openai(prompt: str, system: str, model: str) -> str | None:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": _TEMP,
    }
    try:
        out = _post_json(
            "https://api.openai.com/v1/chat/completions",
            body,
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


def _as_bullets(text: str, max_items: int) -> list[str]:
    """Normalize free-form model output into clean `- ` bullet lines."""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*•").strip()
        # Drop list numbering like "1." / "2)".
        if line[:2].rstrip(".)").isdigit():
            line = line.split(".", 1)[-1].split(")", 1)[-1].strip()
        if line:
            out.append(f"- {line}")
        if len(out) >= max_items:
            break
    return out


def summarize_to_bullets(
    facts: str,
    instruction: str,
    fallback_lines: list[str],
    *,
    strict_local: bool = True,
    max_items: int = 6,
    model: str | None = None,
) -> SummaryResult:
    """Polish locally-extracted `facts` into bullets, or use the fallback.

    `facts` is already-derived, local data (e.g. ranked contacts, keyword
    counts) — never raw private text dumps. `instruction` tells the model how to
    phrase the bullets. If no local model answers, `fallback_lines` (the
    source's own deterministic bullets) are returned verbatim.
    """
    system = (
        "You turn already-summarized facts into a short, plain bullet list for a "
        "voice assistant's private context. Be concise and concrete. Output ONLY "
        f"bullet lines, at most {max_items}. Never invent details not in the facts."
    )
    prompt = f"{instruction}\n\nFACTS:\n{facts}"

    text = _try_ollama(prompt, system, model or OLLAMA_MODEL)
    if text:
        bullets = _as_bullets(text, max_items)
        if bullets:
            return SummaryResult(bullets, "ollama")

    if not strict_local:
        text = _try_openai(prompt, system, model or OPENAI_MODEL)
        if text:
            bullets = _as_bullets(text, max_items)
            if bullets:
                return SummaryResult(bullets, "openai")

    return SummaryResult(fallback_lines[:max_items], "deterministic")


def probe_engine() -> str:
    """Which summarizer engine is currently reachable (for status banners)."""
    if _try_ollama("Reply with the single word: ok", "Reply concisely.", OLLAMA_MODEL):
        return "ollama"
    if os.getenv("OPENAI_API_KEY"):
        return "openai (cloud — used only when strict_local=False)"
    return "deterministic"


if __name__ == "__main__":
    print(f"ingest summarizer engine: {probe_engine()}")
