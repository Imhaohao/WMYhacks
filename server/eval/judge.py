"""LLM-as-judge scoring of a transcript against a metric.

Mirrors how Cekura metrics grade a call, so a local mock run and a real
Cekura run produce the same scorecard shape. The judge is asked for strict
JSON; we parse defensively and fall back to a neutral score on garbage.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from . import llm
from .metrics import Metric
from .personas import Persona
from .simulate import Transcript

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass
class Score:
    metric_id: str
    score: float
    passed: bool
    reasoning: str


def _parse(raw: str, threshold: float) -> tuple[float, bool, str]:
    m = _JSON_RE.search(raw)
    if not m:
        return 0.5, False, f"unparseable judge output: {raw[:120]}"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return 0.5, False, f"invalid JSON from judge: {raw[:120]}"
    score = float(data.get("score", 0.5))
    score = max(0.0, min(1.0, score))
    passed = bool(data.get("passed", score >= threshold))
    reasoning = str(data.get("reasoning", ""))[:300]
    return score, passed, reasoning


def score_metric(transcript: Transcript, metric: Metric, persona: Persona) -> Score:
    system = (
        "JUDGE_METRIC\n"
        "You are a strict evaluator of a voicemail-screening AI agent. "
        "Return ONLY a JSON object: "
        '{"score": <float 0..1>, "passed": <bool>, "reasoning": "<short>"}. '
        "No prose outside the JSON."
    )
    prompt = (
        f"METRIC: {metric.name} (id={metric.id})\n"
        f"RUBRIC:\n{metric.judge_prompt}\n\n"
        f"CALLER: {persona.name} -- {persona.description}\n"
        f"EXPECTED OUTCOME for this caller: {persona.expected_outcome}\n\n"
        f"TRANSCRIPT:\n{transcript.as_text()}\n\n"
        f"pass_threshold={metric.pass_threshold}. Score now."
    )
    raw = llm.complete(prompt, system=system).text
    score, passed, reasoning = _parse(raw, metric.pass_threshold)
    return Score(metric.id, score, passed, reasoning)


def score_all(transcript: Transcript, metrics: list[Metric], persona: Persona) -> list[Score]:
    return [score_metric(transcript, m, persona) for m in metrics]


def to_dict(score: Score) -> dict:
    return asdict(score)
