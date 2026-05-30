"""Tests for the eval harness. Run: uv run pytest eval/test_eval.py

These run offline on the STUB engine, so they pass with no network, no
Ollama, and no API keys -- which is the whole point of the harness design.
The Cekura mapping tests check payload SHAPE only (no network).
"""

from __future__ import annotations

import os

# Force the deterministic engine so tests are hermetic.
os.environ["CEKURA_EVAL_ENGINE"] = "stub"

from eval import judge, llm, simulate  # noqa: E402
from eval.cekura_client import (  # noqa: E402
    all_metric_payloads,
    all_scenario_payloads,
    to_metric_payload,
    to_scenario_payload,
)
from eval.metrics import METRICS  # noqa: E402
from eval.personas import PERSONAS, get  # noqa: E402
from eval.run_evals import evaluate_prompt  # noqa: E402


def test_personas_have_valid_outcomes():
    valid = {"block", "take_message", "allow"}
    assert len(PERSONAS) >= 5
    for p in PERSONAS:
        assert p.expected_outcome in valid
        assert p.caller_system_prompt()


def test_metrics_well_formed():
    assert len(METRICS) == 5
    ids = {m.id for m in METRICS}
    assert "correct_screening" in ids
    for m in METRICS:
        assert 0 < m.pass_threshold <= 1
        assert m.judge_prompt


def test_stub_engine_is_active():
    assert llm.active_engine() == "stub"


def test_simulate_produces_transcript():
    p = get("spammer")
    t = simulate.simulate_call(p, "weak baseline v0 prompt", max_turns=3)
    assert t.turns
    assert t.turns[0]["role"] == "agent"
    assert t.as_text()


def test_judge_returns_score():
    p = get("important_client")
    t = simulate.simulate_call(p, "a decent prompt", max_turns=2)
    scores = judge.score_all(t, METRICS, p)
    assert len(scores) == len(METRICS)
    for s in scores:
        assert 0.0 <= s.score <= 1.0


def test_weak_prompt_scores_below_strong_prompt():
    """Core invariant of the mock demo: the loop has room to improve."""
    weak = evaluate_prompt("weak baseline v0 prompt", label="weak")
    strong = evaluate_prompt(
        "A thorough screening assistant that asks who/what/callback, "
        "blocks spam, takes messages, stays in character.",
        label="strong",
    )
    assert weak.aggregate_score < strong.aggregate_score


def test_cekura_metric_payload_shape():
    """Metric payloads must match the real metrics_create contract."""
    payloads = all_metric_payloads(project_id=99)
    assert len(payloads) == len(METRICS)
    mp = to_metric_payload(METRICS[0], project_id=99, display_order=0)
    assert mp["type"] == "llm_judge"
    assert mp["eval_type"] == "continuous_qualitative"
    assert mp["project"] == 99
    assert mp["description"]  # criterion lives in description, not prompt


def test_cekura_scenario_payload_shape():
    """Scenario payloads must match the real scenarios_create contract."""
    payloads = all_scenario_payloads(personality_id=7, project_id=99)
    assert len(payloads) == len(PERSONAS)
    sc = to_scenario_payload(
        PERSONAS[0], personality_id=7, metric_ids=[1, 2], project_id=99,
        agent_id=None,
    )
    assert sc["personality"] == 7
    assert sc["scenario_type"] == "instruction"
    assert sc["instructions"]
    assert sc["expected_outcome_prompt"]
    assert sc["metrics"] == [1, 2]
    # No agent -> attached to project.
    assert sc["project"] == 99
    # With an agent -> attached to the agent instead.
    sc2 = to_scenario_payload(
        PERSONAS[0], personality_id=7, metric_ids=[], project_id=99, agent_id=42,
    )
    assert sc2["agent"] == 42
