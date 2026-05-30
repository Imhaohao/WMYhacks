"""Local caller<->agent conversation simulator (mock mode).

This stands in for Person 1's live voice agent so the eval + improvement loop
is fully runnable today, with no phone, no WebRTC, and no Cekura. Two LLM
roles talk to each other:

    - the AGENT, driven by the voicemail system prompt under test
    - the CALLER, driven by a Persona

When Person 1's real agent is ready, `run_evals.py --cekura` runs the SAME
personas as Cekura scenarios against the live bot instead (see cekura_client).
The transcript shape produced here matches what the judge expects either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import llm
from .personas import Persona

MAX_TURNS = 8  # caller+agent exchanges before we force-close


@dataclass
class Transcript:
    persona_id: str
    turns: list[dict] = field(default_factory=list)  # {"role": ..., "text": ...}
    engine: str = "stub"

    def as_text(self) -> str:
        lines = []
        for t in self.turns:
            who = "CALLER" if t["role"] == "caller" else "AGENT"
            lines.append(f"{who}: {t['text']}")
        return "\n".join(lines)


def _agent_reply(system_prompt: str, transcript: Transcript) -> llm.LLMResult:
    convo = transcript.as_text()
    prompt = (
        "You are the voicemail assistant. Here is the call so far:\n\n"
        f"{convo}\n\n"
        "Give ONLY your next spoken line (1-2 short sentences). If the call is "
        "complete (message taken or caller declined), end with the token "
        "[END_CALL]."
    )
    return llm.complete(prompt, system=system_prompt)


def _caller_reply(persona: Persona, transcript: Transcript) -> llm.LLMResult:
    convo = transcript.as_text()
    prompt = (
        "Here is the call so far:\n\n"
        f"{convo}\n\n"
        "Give ONLY your next spoken line as the caller (1-2 short sentences). "
        "If you are satisfied or have been declined, end with the token "
        "[END_CALL]."
    )
    return llm.complete(prompt, system=persona.caller_system_prompt())


def simulate_call(persona: Persona, system_prompt: str, max_turns: int = MAX_TURNS) -> Transcript:
    """Run a full simulated call and return the transcript.

    The agent speaks first (greeting), matching the live bot's behavior.
    """
    t = Transcript(persona_id=persona.id)

    greeting = _agent_reply(system_prompt, t)
    t.engine = greeting.engine
    t.turns.append({"role": "agent", "text": greeting.text.replace("[END_CALL]", "").strip()})

    for _ in range(max_turns):
        caller = _caller_reply(persona, t)
        caller_text = caller.text
        t.turns.append({"role": "caller", "text": caller_text.replace("[END_CALL]", "").strip()})
        if "[END_CALL]" in caller_text:
            break

        agent = _agent_reply(system_prompt, t)
        agent_text = agent.text
        t.turns.append({"role": "agent", "text": agent_text.replace("[END_CALL]", "").strip()})
        if "[END_CALL]" in agent_text:
            break

    return t


if __name__ == "__main__":
    from .personas import PERSONAS

    sp = "You are a voicemail assistant. Take a message and be friendly."
    tr = simulate_call(PERSONAS[0], sp, max_turns=3)
    print(f"[engine={tr.engine}]")
    print(tr.as_text())
