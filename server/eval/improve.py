"""The auto-improvement loop -- the demo finale (PLAN.md Person 2, Part C).

    1. Evaluate the current agent prompt  -> baseline scorecard
    2. Collect the failing metrics + the transcripts that caused them
    3. Ask an LLM to REWRITE the system prompt to fix those failures
    4. Re-evaluate the rewritten prompt   -> improved scorecard
    5. Show the before/after: which metrics flipped FAIL -> PASS

Repeats for --rounds iterations or until everything passes. Each prompt
version is saved to eval/prompts/voicemail_agent_vN.txt so the improvement
is an auditable artifact (and Person 3 can persist the history to AWS).

This is designed to run live on stage as a single command:

    uv run python -m eval.improve
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import llm
from .run_evals import (
    DEFAULT_PROMPT,
    HERE,
    Scorecard,
    evaluate_prompt,
    print_scorecard,
    save_scorecard,
)

PROMPTS_DIR = HERE / "prompts"


def collect_failures(card: Scorecard) -> str:
    """Human-readable digest of what failed and why, fed to the rewriter."""
    lines: list[str] = []
    for r in card.persona_results:
        fails = [s for s in r.scores if not s["passed"]]
        if not fails:
            continue
        lines.append(f"\nCaller '{r.persona_name}' (expected: {r.expected_outcome}):")
        for s in fails:
            lines.append(f"  - FAILED {s['metric_id']} (score {s['score']:.2f}): {s['reasoning']}")
        # Include a short transcript excerpt for grounding.
        excerpt = " | ".join(f"{t['role']}: {t['text']}" for t in r.transcript[:4])
        lines.append(f"    transcript start: {excerpt}")
    return "\n".join(lines) if lines else "(no failures)"


def rewrite_prompt(current_prompt: str, failures: str) -> llm.LLMResult:
    system = (
        "IMPROVE_PROMPT\n"
        "You are a prompt engineer improving the SYSTEM PROMPT of a voicemail "
        "screening voice agent. You will be given the current prompt and a list "
        "of evaluation failures. Rewrite the system prompt so those failures "
        "are fixed, while keeping it concise and suitable for a low-latency "
        "voice agent.\n"
        "Rules: output ONLY the new system prompt text -- no preamble, no "
        "markdown fences, no commentary. Keep it under ~250 words. Preserve "
        "anything that already works."
    )
    prompt = (
        "CURRENT SYSTEM PROMPT:\n"
        "-----\n"
        f"{current_prompt}\n"
        "-----\n\n"
        "EVALUATION FAILURES TO FIX:\n"
        f"{failures}\n\n"
        "Write the improved system prompt now."
    )
    return llm.complete(prompt, system=system)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n```$", "", text)
    return text.strip()


def _next_version(start: str) -> int:
    existing = list(PROMPTS_DIR.glob("voicemail_agent_v*.txt"))
    nums = []
    for p in existing:
        m = re.search(r"_v(\d+)\.txt$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def _flips(before: Scorecard, after: Scorecard) -> list[str]:
    """Metrics that went FAIL -> PASS, as 'persona/metric' strings."""

    def passmap(card: Scorecard) -> dict[tuple[str, str], bool]:
        out = {}
        for r in card.persona_results:
            for s in r.scores:
                out[(r.persona_id, s["metric_id"])] = s["passed"]
        return out

    b, a = passmap(before), passmap(after)
    return [f"{pid}/{mid}" for (pid, mid), was in b.items() if not was and a.get((pid, mid))]


def run_loop(prompt_path: str, rounds: int) -> None:
    current_path = Path(prompt_path)
    current_prompt = current_path.read_text().strip()

    print("\n#### AUTO-IMPROVEMENT LOOP ####")
    print(f"Engine: {llm.active_engine()}   Mode: mock   Rounds: {rounds}")

    baseline = evaluate_prompt(
        current_prompt,
        label="v0-baseline",
        prompt_path=str(current_path),
    )
    print_scorecard(baseline)
    save_scorecard(baseline)

    prev = baseline
    for rnd in range(1, rounds + 1):
        failures = collect_failures(prev)
        if failures == "(no failures)":
            print("\nAll metrics already pass -- nothing to improve. Done.")
            break

        print(f"\n--- Round {rnd}: rewriting prompt to fix {prev.pass_rate:.0%} pass rate ---")
        rewritten = _strip_fences(rewrite_prompt(prev.prompt_text, failures).text)

        version = _next_version("voicemail_agent")
        new_path = PROMPTS_DIR / f"voicemail_agent_v{version}.txt"
        new_path.write_text(rewritten + "\n")
        print(f"Wrote improved prompt -> {new_path}")

        improved = evaluate_prompt(
            rewritten,
            label=f"v{version}-improved",
            prompt_path=str(new_path),
        )
        print_scorecard(improved)
        save_scorecard(improved)

        flips = _flips(prev, improved)
        print(f"\n>>> Round {rnd} result:")
        print(f"    aggregate {prev.aggregate_score:.3f} -> {improved.aggregate_score:.3f}")
        print(f"    pass rate {prev.pass_rate:.0%} -> {improved.pass_rate:.0%}")
        if flips:
            print(f"    FLIPPED FAIL->PASS: {', '.join(flips)}")
        else:
            print("    (no metric flipped to PASS this round)")

        prev = improved

    print("\n#### LOOP COMPLETE ####")
    print(f"Final aggregate score: {prev.aggregate_score:.3f} ({prev.pass_rate:.0%} pass rate)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Auto-improve the voicemail agent.")
    ap.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()
    run_loop(args.prompt, args.rounds)


if __name__ == "__main__":
    main()
