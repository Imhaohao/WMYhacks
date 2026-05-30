"""Scoring metrics: what "good" means for the voicemail agent.

Each metric is an LLM-as-judge definition. `judge_prompt` is the rubric the
judge applies to a transcript; the judge must return strict JSON:

    {"score": <0..1 float>, "passed": <bool>, "reasoning": "<short>"}

`pass_threshold` is the score at/above which the metric is considered passed
(used when a judge returns a score but not an explicit boolean).

These mirror the five metrics in PLAN.md and are exported to Cekura via
cekura_client.to_metric_payload() (created live through the metrics_create
MCP tool).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Metric:
    id: str
    name: str
    description: str
    judge_prompt: str
    pass_threshold: float = 0.7
    weight: float = 1.0  # relative weight in the aggregate score


METRICS: list[Metric] = [
    Metric(
        id="correct_screening",
        name="Correct Screening",
        description=(
            "Did the agent correctly classify the caller (spam vs. legitimate) and act accordingly?"
        ),
        judge_prompt=(
            "Evaluate whether the agent correctly SCREENED this caller.\n"
            "You are given the EXPECTED outcome for this caller:\n"
            "  - block        = spam/sales/wrong-number: agent should politely "
            "decline and NOT take a real message.\n"
            "  - take_message = legitimate caller: agent should capture a "
            "message.\n"
            "  - allow        = urgent/important: agent should capture a "
            "message AND treat it as priority.\n"
            "Score 1.0 if the agent's behavior matches the expected outcome, "
            "0.0 if it does the opposite (e.g. takes a message for a spammer, "
            "or blocks a legitimate client). Partial credit for partial "
            "matches."
        ),
        pass_threshold=0.7,
        weight=1.5,
    ),
    Metric(
        id="message_captured",
        name="Message Captured",
        description=(
            "For callers who should leave a message, did the agent capture a "
            "usable message (who / what / callback)?"
        ),
        judge_prompt=(
            "Evaluate message capture quality. A usable message has THREE "
            "parts: (1) WHO is calling, (2) WHAT it is regarding, (3) a "
            "CALLBACK number or contact.\n"
            "If the expected outcome is 'block', this metric is N/A -> return "
            "score 1.0 and passed true (no message needed).\n"
            "Otherwise score = fraction of the three parts the agent actually "
            "collected. Reward reading the callback number back to confirm."
        ),
        pass_threshold=0.7,
        weight=1.2,
    ),
    Metric(
        id="persona_consistency",
        name="Persona Consistency",
        description=(
            "Did the agent stay in character as the owner's personal assistant throughout?"
        ),
        judge_prompt=(
            "Evaluate whether the agent stayed in character as the OWNER'S "
            "personal voicemail assistant. Penalize: revealing it is an AI/LLM, "
            "breaking character, claiming to BE the owner, or generic "
            "call-center behavior. Score 1.0 for fully consistent, lower for "
            "slips."
        ),
        pass_threshold=0.8,
        weight=1.0,
    ),
    Metric(
        id="caller_sentiment",
        name="Caller Sentiment / Respect",
        description=(
            "Was the caller treated respectfully and left with a reasonable "
            "impression, even when declined?"
        ),
        judge_prompt=(
            "Evaluate how respectfully the caller was treated. Even spammers "
            "and declined salespeople should be handled politely and without "
            "rudeness. Score 1.0 for warm/respectful throughout, lower for "
            "curt, dismissive, or hostile turns."
        ),
        pass_threshold=0.7,
        weight=0.8,
    ),
    Metric(
        id="task_completion",
        name="Task Completion",
        description="Did the call achieve its correct purpose end to end?",
        judge_prompt=(
            "Holistically, did the call reach the correct end state for this "
            "caller? For 'block': caller was declined and the call ended "
            "cleanly. For 'take_message'/'allow': a complete message was taken "
            "and the call ended cleanly. Score 1.0 for a clean correct outcome, "
            "lower for incomplete, looping, or wrong outcomes."
        ),
        pass_threshold=0.7,
        weight=1.0,
    ),
]


METRICS_BY_ID = {m.id: m for m in METRICS}


def get(metric_id: str) -> Metric:
    return METRICS_BY_ID[metric_id]


if __name__ == "__main__":
    for m in METRICS:
        print(f"{m.id:22s} thr={m.pass_threshold} w={m.weight}  {m.name}")
