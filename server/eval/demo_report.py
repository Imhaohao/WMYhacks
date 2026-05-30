"""Run the local Cekura eval loop (Track 1) and compile ONE markdown report.

A single command produces an at-a-glance "lab notebook" of how we used Cekura
during development: a baseline agent prompt is scored against every caller
persona, the failures feed an LLM that rewrites the prompt, and we re-score --
the report shows, per scenario and per metric, how scores change across prompt
versions, plus the full prompt text for each version.

    uv run python -m eval.demo_report
    uv run python -m eval.demo_report --rounds 2 --out eval/results/demo_report.md

Mock mode only (local simulator + local LLM-as-judge). Pick the engine with
CEKURA_EVAL_ENGINE=stub|ollama|openai|auto. Real Cekura runs are MCP-driven;
this is the *development-process* story you show alongside the live Cekura
production dashboard.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from . import llm
from .improve import (
    _flips,
    _strip_fences,
    collect_failures,
    rewrite_prompt,
)
from .metrics import METRICS
from .personas import PERSONAS
from .run_evals import (
    DEFAULT_PROMPT,
    HERE,
    RESULTS_DIR,
    Scorecard,
    evaluate_prompt,
    save_scorecard,
)

PROMPTS_DIR = HERE / "prompts"
METRIC_NAME = {m.id: m.name for m in METRICS}
DEFAULT_OUT = RESULTS_DIR / "demo_report.md"


# --- run the experiments -------------------------------------------------


def run_experiments(prompt_path: str | Path, rounds: int) -> list[Scorecard]:
    """Baseline + up to `rounds` auto-improved prompt versions.

    Reuses the eval.improve loop logic (rewrite -> re-score) but COLLECTS the
    scorecards so we can render them into one report. Report versions are
    labelled v0..vN by ROUND (not by the global vN history), and improved
    prompts are written to a self-contained `voicemail_agent_demo_v{i}.txt`
    namespace that is overwritten on each run -- so the report always reads
    cleanly as v0 -> v1 -> ... and the demo is repeatable.
    """
    current_path = Path(prompt_path)
    cards: list[Scorecard] = []

    baseline = evaluate_prompt(
        current_path.read_text().strip(),
        label="v0-baseline",
        prompt_path=str(current_path),
    )
    save_scorecard(baseline)
    cards.append(baseline)

    prev = baseline
    for i in range(1, rounds + 1):
        failures = collect_failures(prev)
        if failures == "(no failures)":
            break
        rewritten = _strip_fences(rewrite_prompt(prev.prompt_text, failures).text)
        new_path = PROMPTS_DIR / f"voicemail_agent_demo_v{i}.txt"
        new_path.write_text(rewritten + "\n")
        improved = evaluate_prompt(
            rewritten,
            label=f"v{i}-improved",
            prompt_path=str(new_path),
        )
        save_scorecard(improved)
        cards.append(improved)
        prev = improved

    return cards


# --- render the markdown -------------------------------------------------


def _short(card: Scorecard) -> str:
    """'v0-baseline' -> 'v0'."""
    return card.label.split("-", 1)[0]


def _mark(passed: bool) -> str:
    return "✅" if passed else "❌"


def _persona_lookup(card: Scorecard) -> dict[str, dict[str, dict]]:
    """{persona_id: {metric_id: score_dict}} for one scorecard."""
    out: dict[str, dict[str, dict]] = {}
    for r in card.persona_results:
        out[r.persona_id] = {s["metric_id"]: s for s in r.scores}
    return out


def _weighted_lookup(card: Scorecard) -> dict[str, float]:
    return {r.persona_id: r.weighted_score for r in card.persona_results}


def render_markdown(cards: list[Scorecard]) -> str:
    versions = [_short(c) for c in cards]
    engine = cards[0].engine
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    # ── Header ───────────────────────────────────────────────────────────────
    lines += [
        "# Cekura Development Loop — Voicemail Screening Agent",
        "",
        f"*Generated {ts} · mode=mock · engine=`{engine}` · "
        f"{len(PERSONAS)} scenarios × {len(METRICS)} metrics*",
        "",
        "We use **Cekura** in two places, scored on the *same* metrics:",
        "",
        "1. **Simulation (this report).** Caller personas are run against the "
        "agent prompt and graded. Failures feed an LLM that rewrites the prompt; "
        "we re-run and watch the scores climb — an auditable dev loop.",
        "2. **Production (live dashboard).** Real calls are ingested into Cekura "
        "Observability and graded by the same metrics.",
        "",
    ]
    if engine == "stub":
        lines += [
            "> ⚠️ **Offline `stub` engine** — no Ollama/OpenAI reachable, so these "
            "numbers are *illustrative of the loop's shape* (failures → rewrite → "
            "flips to PASS), not real model scores. Set `OPENAI_API_KEY` or bring "
            "up Ollama for meaningful numbers.",
            "",
        ]

    # ── Progression at a glance ────────────────────────────────────────────────
    lines += [
        "## Progression at a glance",
        "",
        "| Version | Prompt file | Aggregate | Pass rate | FAIL→PASS this step |",
        "|---|---|---:|---:|---|",
    ]
    for i, c in enumerate(cards):
        if i == 0:
            flip_cell = "— (baseline)"
        else:
            flips = _flips(cards[i - 1], c)
            flip_cell = f"**{len(flips)}** checks" if flips else "0"
        lines.append(
            f"| {_short(c)} | `{Path(c.prompt_path).name}` | "
            f"{c.aggregate_score:.3f} | {c.pass_rate:.0%} | {flip_cell} |"
        )
    lines.append("")

    # ── Per-metric pass rate across versions ───────────────────────────────────
    lines += [
        "## Per-metric pass rate across versions",
        "",
        "| Metric | " + " | ".join(versions) + " |",
        "|---|" + "---:|" * len(versions),
    ]
    for m in METRICS:
        cells = " | ".join(f"{c.metric_pass_rate.get(m.id, 0):.0%}" for c in cards)
        lines.append(f"| {m.name} | {cells} |")
    lines.append("")

    # ── Per-scenario detail ────────────────────────────────────────────────────
    lines += [
        "## Per-scenario detail",
        "",
        "Each cell is the metric score with PASS/FAIL. Watch cells flip ❌→✅ "
        "left-to-right as the prompt improves.",
        "",
    ]
    lookups = [_persona_lookup(c) for c in cards]
    wlookups = [_weighted_lookup(c) for c in cards]
    for p in PERSONAS:
        lines += [
            f"### {p.name} — expects **{p.expected_outcome}**",
            "",
            "| Metric | " + " | ".join(versions) + " |",
            "|---|" + "---:|" * len(versions),
        ]
        for m in METRICS:
            cells = []
            for lk in lookups:
                s = lk.get(p.id, {}).get(m.id)
                cells.append(f"{s['score']:.2f} {_mark(s['passed'])}" if s else "—")
            lines.append(f"| {m.name} | " + " | ".join(cells) + " |")
        wcells = " | ".join(f"**{wl.get(p.id, 0):.2f}**" for wl in wlookups)
        lines.append(f"| _weighted_ | {wcells} |")
        lines.append("")

    # ── Prompt versions (full text) ────────────────────────────────────────────
    lines += [
        "## Prompt versions",
        "",
        "The exact system prompt evaluated at each step (the artifact that "
        "changes between versions).",
        "",
    ]
    for c in cards:
        tag = "baseline" if _short(c) == "v0" else "auto-improved"
        lines += [
            f"### {_short(c)} — {tag} (`{Path(c.prompt_path).name}`)",
            "",
            "```text",
            c.prompt_text.strip(),
            "```",
            "",
        ]

    return "\n".join(lines)


# --- CLI -----------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run the local Cekura eval loop and compile one markdown report."
    )
    ap.add_argument("--prompt", default=str(DEFAULT_PROMPT), help="starting agent prompt")
    ap.add_argument("--rounds", type=int, default=1, help="auto-improvement rounds")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="markdown output path")
    args = ap.parse_args()

    print(f"Engine: {llm.active_engine()}   Rounds: {args.rounds}")
    print("Running experiments (this simulates + scores every persona per version)...")
    cards = run_experiments(args.prompt, args.rounds)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(cards))

    print(f"\nVersions evaluated: {', '.join(_short(c) for c in cards)}")
    for c in cards:
        print(f"  {_short(c):<4} aggregate={c.aggregate_score:.3f}  pass={c.pass_rate:.0%}")
    print(f"\nReport written -> {out_path}")


if __name__ == "__main__":
    main()
