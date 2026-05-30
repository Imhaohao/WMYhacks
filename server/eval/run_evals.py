"""Orchestrator: run every persona against an agent prompt and score it.

Produces a Scorecard (per-persona, per-metric, plus weighted aggregate),
prints a readable table, and writes JSON to eval/results/ for the
auto-improvement loop and for Person 3's AWS persistence to pick up.

This harness runs **mock mode only**: it simulates calls locally
(`eval.simulate`) and scores them with a local LLM-as-judge (`eval.judge`).
Real Cekura runs are driven by the Cekura **MCP tools in-session**, not from
Python -- see `eval.cekura_client.PROVISION_PLAN`.

Usage:
    uv run python -m eval.run_evals
    uv run python -m eval.run_evals --prompt eval/prompts/voicemail_agent_v0.txt
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import judge, llm, simulate
from .metrics import METRICS, Metric
from .personas import PERSONAS, Persona

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
DEFAULT_PROMPT = HERE / "prompts" / "voicemail_agent_v0.txt"


@dataclass
class PersonaResult:
    persona_id: str
    persona_name: str
    expected_outcome: str
    scores: list[dict]  # judge.Score as dict
    transcript: list[dict]
    weighted_score: float
    passed_count: int
    total_count: int


@dataclass
class Scorecard:
    label: str
    mode: str
    engine: str
    prompt_path: str
    prompt_text: str
    created_at: str
    persona_results: list[PersonaResult] = field(default_factory=list)
    aggregate_score: float = 0.0
    pass_rate: float = 0.0
    metric_pass_rate: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _weighted(scores: list[judge.Score], metrics: list[Metric]) -> float:
    by_id = {m.id: m for m in metrics}
    num = sum(s.score * by_id[s.metric_id].weight for s in scores)
    den = sum(by_id[s.metric_id].weight for s in scores) or 1.0
    return round(num / den, 3)


def evaluate_prompt(
    prompt_text: str,
    *,
    label: str,
    personas: list[Persona] = PERSONAS,
    metrics: list[Metric] = METRICS,
    prompt_path: str = "",
) -> Scorecard:
    """Run all personas against `prompt_text`, score every metric, aggregate.

    Mock mode: local simulator + local LLM-as-judge (illustrative). Real Cekura
    runs are driven via the MCP tools in-session, not here.
    """
    engine = llm.active_engine()
    card = Scorecard(
        label=label,
        mode="mock",
        engine=engine,
        prompt_path=prompt_path,
        prompt_text=prompt_text,
        created_at=datetime.now(UTC).isoformat(),
    )

    metric_passes: dict[str, int] = {m.id: 0 for m in metrics}

    for p in personas:
        transcript = simulate.simulate_call(p, prompt_text)

        scores = judge.score_all(transcript, metrics, p)
        for s in scores:
            if s.passed:
                metric_passes[s.metric_id] += 1

        passed = sum(1 for s in scores if s.passed)
        card.persona_results.append(
            PersonaResult(
                persona_id=p.id,
                persona_name=p.name,
                expected_outcome=p.expected_outcome,
                scores=[judge.to_dict(s) for s in scores],
                transcript=transcript.turns,
                weighted_score=_weighted(scores, metrics),
                passed_count=passed,
                total_count=len(scores),
            )
        )

    n_personas = len(personas)
    n_checks = n_personas * len(metrics)
    total_passed = sum(metric_passes.values())
    card.aggregate_score = round(
        sum(r.weighted_score for r in card.persona_results) / (n_personas or 1), 3
    )
    card.pass_rate = round(total_passed / (n_checks or 1), 3)
    card.metric_pass_rate = {
        mid: round(c / (n_personas or 1), 3) for mid, c in metric_passes.items()
    }
    return card


# --- presentation --------------------------------------------------------


def print_scorecard(card: Scorecard) -> None:
    metrics = METRICS
    print()
    print("=" * 78)
    print(f" SCORECARD: {card.label}   [mode={card.mode} engine={card.engine}]")
    print("=" * 78)
    header = f"{'persona':<22}" + "".join(f"{m.id[:10]:>11}" for m in metrics) + f"{'wt':>7}"
    print(header)
    print("-" * len(header))
    for r in card.persona_results:
        by_id = {s["metric_id"]: s for s in r.scores}
        row = f"{r.persona_name[:22]:<22}"
        for m in metrics:
            s = by_id.get(m.id, {})
            mark = "P" if s.get("passed") else "F"
            row += f"{s.get('score', 0):.2f}{mark:>1}".rjust(11)
        row += f"{r.weighted_score:>7.2f}"
        print(row)
    print("-" * len(header))
    print(f"Aggregate weighted score : {card.aggregate_score:.3f}")
    print(f"Overall metric pass rate : {card.pass_rate:.1%}")
    print("Per-metric pass rate:")
    for m in metrics:
        print(f"   {m.id:<22} {card.metric_pass_rate.get(m.id, 0):.0%}")
    print("=" * 78)


def save_scorecard(card: Scorecard, name: str | None = None) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    fname = name or f"scorecard-{card.label}-{stamp}.json"
    path = RESULTS_DIR / fname
    path.write_text(json.dumps(card.to_dict(), indent=2))
    return path


def load_prompt(path: str | Path) -> str:
    return Path(path).read_text().strip()


# --- CLI -----------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Run voicemail-agent evals.")
    ap.add_argument(
        "--prompt", default=str(DEFAULT_PROMPT), help="path to the agent system prompt to evaluate"
    )
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    prompt_text = load_prompt(args.prompt)
    card = evaluate_prompt(
        prompt_text,
        label=args.label,
        prompt_path=args.prompt,
    )
    print_scorecard(card)
    if not args.no_save:
        path = save_scorecard(card)
        print(f"\nSaved scorecard -> {path}")
    if card.engine == "stub":
        print(
            "\nNOTE: running on the offline STUB engine (no Ollama/OpenAI "
            "reachable). Numbers are illustrative. Bring up Ollama "
            "(compute-box) or set OPENAI_API_KEY for real scoring."
        )


if __name__ == "__main__":
    main()
