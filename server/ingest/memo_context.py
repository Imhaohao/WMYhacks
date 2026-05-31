"""Privacy-safe derived facts shared by local agent-memo sources.

The adapters may inspect owner-authored prompts locally, but the summarizer
receives only coarse aggregate signals. A separate bounded lingo profile may
retain short phrases repeated across multiple prompts; raw prompts and one-off
project detail stay out of persona_context.md.
"""

from __future__ import annotations

import statistics

from .voice_lingo import derive_lingo, render_lingo_lines

_WORKFLOW_SIGNALS = (
    "delegate",
    "subagent",
    "parallel",
    "background",
    "plan",
    "implement",
    "review",
    "test",
    "verify",
    "debug",
    "deploy",
    "concise",
)


def derive_prompt_facts(prompts: list[str]) -> tuple[str, list[str]]:
    """Return aggregate facts and deterministic bullets for owner prompts."""
    if not prompts:
        return "", []

    lengths = [len(prompt.split()) for prompt in prompts]
    typical = statistics.median(lengths)
    brevity = "very concise" if typical < 12 else "moderate" if typical < 40 else "detailed"

    joined = " ".join(prompts).lower()
    signals = [signal for signal in _WORKFLOW_SIGNALS if signal in joined]
    facts = (
        f"prompt count: {len(prompts)}\n"
        f"typical prompt length: {typical:.0f} words ({brevity})\n"
        f"workflow signals present: {', '.join(signals) or 'none'}"
    )
    fallback = [
        f"- Communicates in a {brevity} style (~{typical:.0f} words per request, median).",
    ]
    if signals:
        fallback.append(f"- Common workflow signals: {', '.join(signals[:8])}.")
    return facts, fallback


def derive_prompt_lingo_lines(prompts: list[str], source_label: str) -> list[str]:
    """Return a compact Persona block from recurring owner prompt lingo."""
    # Prompt logs contain lots of repeated technical vocabulary. Learn only
    # conversational markers and recognized style phrases from them; broader
    # recurring snippets are appropriate for message and sent-mail sources.
    profile = derive_lingo(prompts, allow_acronyms=False, allow_generic=False)
    return render_lingo_lines(profile, source_label)
