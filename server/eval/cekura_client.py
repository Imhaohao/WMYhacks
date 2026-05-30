"""Map our personas/metrics to verified Cekura payloads (no network here).

The live Cekura path is **driven by the Cekura MCP tools in-session**, not by
this module. This file is intentionally network-free: it only

  1. builds the exact request payloads for `metrics_create` / `scenarios_create`
     from our local `Metric` / `Persona` models (shapes verified against the
     live MCP tool schemas), and
  2. documents the provisioning sequence as data (`PROVISION_PLAN`), and
  3. loads/saves the resulting Cekura IDs to `eval/cekura_ids.json` once an agent
     (the executive driving the MCP tools) has actually created the objects.

The Python eval harness itself (`run_evals.py`, `improve.py`) runs **mock mode
only** -- local simulator + local LLM-as-judge. Real provisioning and real runs
happen through the MCP tools:

    metrics_create, scenarios_create, scenarios_run_text / _pipecat_v1 / _v2,
    results_retrieve, runs_improve_prompt

Why no REST client lives here anymore: a previous hand-rolled urllib client
guessed endpoints and, worse, auto-derived the project id from a *foreign*
scenario -- which picked up a shared project the key could not write to (403).
Project selection must be an OWNED project; never derive it from someone else's
scenarios. The MCP tools carry the verified contract, so we use them directly.

Inspect the payloads any time (no network):  uv run python -m eval.cekura_client
"""

from __future__ import annotations

import json
from pathlib import Path

from .metrics import METRICS, Metric
from .personas import PERSONAS, Persona

HERE = Path(__file__).resolve().parent
IDS_FILE = HERE / "cekura_ids.json"


# --- the live provisioning plan (executed via MCP tools, in-session) -----
#
# Documented as data so the README and any driver agent stay in sync with the
# verified contract. Each step names the MCP tool and what it consumes/produces.
PROVISION_PLAN: list[dict] = [
    {
        "step": 1,
        "tool": "user_organizations_list / projects_list",
        "purpose": "Pick a project the API key OWNS (can write to). Never "
                   "auto-derive it from a foreign scenario -- that caused a 403.",
        "produces": "project_id",
    },
    {
        "step": 2,
        "tool": "personalities_list",
        "purpose": "Pick a caller personality id (global or org-owned).",
        "produces": "personality_id",
    },
    {
        "step": 3,
        "tool": "metrics_create  (x5)",
        "purpose": "Create the 5 llm_judge metrics from all_metric_payloads().",
        "produces": "metrics: {our_metric_id -> cekura_metric_id}",
    },
    {
        "step": 4,
        "tool": "scenarios_create  (x7)",
        "purpose": "Create the 7 persona scenarios from all_scenario_payloads(), "
                   "passing the metric ids; attach to the project (or to an "
                   "agent once Person 1's bot is registered).",
        "produces": "scenarios: {persona_id -> cekura_scenario_id}",
    },
    {
        "step": 5,
        "tool": "aiagents_create",
        "purpose": "Register Person 1's bot (self_hosted / pipecat) when it "
                   "exists, so scenarios can run against it.",
        "produces": "agent_id",
    },
    {
        "step": 6,
        "tool": "scenarios_run_text / scenarios_run_pipecat_v1 / _v2",
        "purpose": "Run the scenarios against the live agent over the matching "
                   "transport.",
        "produces": "result_id",
    },
    {
        "step": 7,
        "tool": "results_retrieve",
        "purpose": "Read genuine server-side scores from "
                   "runs[].evaluation.metrics[].result (runs is a dict keyed by "
                   "run-id string).",
        "produces": "per-metric scores",
    },
    {
        "step": 8,
        "tool": "runs_improve_prompt",
        "purpose": "On failures, regenerate the agent prompt, then re-run and "
                   "re-score -- the auto-improvement loop.",
        "produces": "improved prompt -> re-run",
    },
]


# --- mapping: our model -> REAL Cekura payloads --------------------------

# Ground-truth outcome -> success criterion text Cekura's judge evaluates.
_OUTCOME_SUCCESS = {
    "block": (
        "The agent recognizes this caller as spam/sales/wrong-number, politely "
        "declines, and does NOT take a real message."
    ),
    "take_message": (
        "The agent captures a complete message (who is calling, what it is "
        "about, and a callback number) and ends the call cleanly."
    ),
    "allow": (
        "The agent treats this as urgent/important, captures a complete "
        "message, conveys priority to the owner, and ends cleanly."
    ),
}


def to_metric_payload(m: Metric, project_id: int, display_order: int) -> dict:
    """Metric -> metrics_create payload (llm_judge, project-level).

    Field shapes verified against the live `metrics_create` schema:
    `eval_type=continuous_qualitative` is a valid enum; the evaluation criterion
    lives in `description` (not `prompt`); metrics are project-scoped.
    """
    return {
        "name": m.name,
        "type": "llm_judge",
        # continuous score in [0,1]; matches our 0..1 rubric + thresholds.
        "eval_type": "continuous_qualitative",
        "description": (
            f"{m.judge_prompt}\n\nReturn a score in [0,1] where >= "
            f"{m.pass_threshold} means PASS."
        ),
        "project": project_id,
        "simulation_enabled": True,
        "observability_enabled": True,
        "audio_enabled": False,
        "display_order": display_order,
        "configuration": {},
        "prompt": "",
        "assistant_id": "",
    }


def to_scenario_payload(
    p: Persona, personality_id: int, metric_ids: list[int],
    project_id: int, agent_id: int | None,
) -> dict:
    """Persona -> scenarios_create payload (instruction mode).

    Attaches to `agent` when an agent id is given, else to `project`.
    """
    instructions = (
        f"{p.caller_system_prompt()}\n\n"
        f"Your specific behavior: {p.script_direction}\n"
        f"Your goal: {p.goal}"
    )
    payload: dict = {
        "name": f"{p.name} ({p.id})",
        "scenario_type": "instruction",
        "instructions": instructions,
        "personality": personality_id,
        "metrics": metric_ids,
        "expected_outcome_prompt": _OUTCOME_SUCCESS[p.expected_outcome],
        "tags": p.tags + [f"expect:{p.expected_outcome}", f"diff:{p.difficulty}"],
    }
    if agent_id is not None:
        payload["agent"] = agent_id
    else:
        payload["project"] = project_id
    return payload


# Convenience for inspection/tests (no network).
def all_metric_payloads(project_id: int = 0) -> list[dict]:
    return [to_metric_payload(m, project_id, i) for i, m in enumerate(METRICS)]


def all_scenario_payloads(personality_id: int = 0, project_id: int = 0) -> list[dict]:
    return [
        to_scenario_payload(p, personality_id, [], project_id, None)
        for p in PERSONAS
    ]


# --- ID cache (written once the MCP tools have created the objects) ------


def load_ids() -> dict:
    try:
        return json.loads(IDS_FILE.read_text())
    except OSError:
        return {}


def save_ids(ids: dict) -> None:
    IDS_FILE.write_text(json.dumps(ids, indent=2))


# --- CLI: inspect payloads + plan (no network) ---------------------------


def main() -> None:
    print(f"Metrics defined  : {len(METRICS)}")
    print(f"Personas defined : {len(PERSONAS)}")
    ids = load_ids()
    print(f"Cached IDs file  : {'present' if ids else 'none'} ({IDS_FILE})")
    print("\nProvisioning is driven via the Cekura MCP tools in-session.")
    print("Plan:")
    for s in PROVISION_PLAN:
        print(f"  {s['step']}. {s['tool']:<45} -> {s['produces']}")
    print("\nExample metric payload:")
    print(json.dumps(all_metric_payloads(project_id=0)[0], indent=2))
    print("\nExample scenario payload:")
    print(json.dumps(all_scenario_payloads(personality_id=0, project_id=0)[0], indent=2))


if __name__ == "__main__":
    main()
