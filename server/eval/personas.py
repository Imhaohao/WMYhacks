"""Caller personas for simulating inbound calls to the voicemail agent.

Each persona is a test case: an AI caller with a goal and a behavioral
direction, plus the GROUND TRUTH of how a good screening agent should
handle them (`expected_outcome`). The judge uses `expected_outcome` to
score the `correct_screening` metric objectively.

`expected_outcome` values:
    "block"        -> spam/sales; decline, do NOT take a message
    "take_message" -> legitimate; capture a complete message
    "allow"        -> urgent/important; capture message AND flag as priority

These map cleanly onto Cekura scenarios (see cekura_client.to_scenario_payload).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Persona:
    id: str
    name: str
    description: str
    goal: str  # what the caller is trying to achieve
    script_direction: str  # how the simulated caller should behave
    expected_outcome: str  # ground truth: block | take_message | allow
    difficulty: str = "medium"
    tags: list[str] = field(default_factory=list)

    def caller_system_prompt(self) -> str:
        """System prompt that makes an LLM role-play this caller."""
        return (
            "CALLER_PERSONA\n"
            f"You are role-playing a phone CALLER named '{self.name}'.\n"
            f"Who you are: {self.description}\n"
            f"Your goal on this call: {self.goal}\n"
            f"How to behave: {self.script_direction}\n\n"
            "You are calling someone and reached their voicemail assistant. "
            "Speak naturally, one short turn at a time, like a real phone call. "
            "Do not break character. Do not narrate. Just say your line."
        )


PERSONAS: list[Persona] = [
    Persona(
        id="spammer",
        name="The Spammer",
        description="An auto-warranty / robocall telemarketer.",
        goal="Pitch a vehicle extended-warranty offer and get a 'yes'.",
        script_direction=(
            "Open with a canned promo about the recipient's car warranty "
            "expiring. Ignore questions, keep pitching, never give a real "
            "callback reason. You are not a real personal contact."
        ),
        expected_outcome="block",
        difficulty="easy",
        tags=["spam", "robocall"],
    ),
    Persona(
        id="important_client",
        name="The Important Client",
        description="A real business client with a time-sensitive deal.",
        goal="Reach the owner about signing a contract before end of day.",
        script_direction=(
            "Be professional and concise. Give your name, your company, the "
            "contract reference, and a callback number when asked. This is a "
            "legitimate, high-value call that must be captured accurately."
        ),
        expected_outcome="allow",
        difficulty="medium",
        tags=["legit", "business", "priority"],
    ),
    Persona(
        id="friend",
        name="The Friend",
        description="A casual personal friend of the owner.",
        goal="Leave a friendly message about weekend dinner plans.",
        script_direction=(
            "Warm and informal. Give your first name. Happy to leave a "
            "message. Mention you'll text too. No urgency."
        ),
        expected_outcome="take_message",
        difficulty="easy",
        tags=["legit", "personal"],
    ),
    Persona(
        id="vague_caller",
        name="The Vague Caller",
        description="A caller who won't clearly state their purpose.",
        goal="Get put through without explaining why.",
        script_direction=(
            "Be evasive. When asked what it's regarding, say things like "
            "'it's personal' or 'he'll know what it's about'. Resist giving "
            "details but are not openly hostile. A good agent should still "
            "extract a name and callback and take a message."
        ),
        expected_outcome="take_message",
        difficulty="hard",
        tags=["ambiguous"],
    ),
    Persona(
        id="persistent_salesperson",
        name="The Persistent Salesperson",
        description="A B2B SaaS rep who won't take no for an answer.",
        goal="Book a demo no matter what; refuse to be screened out.",
        script_direction=(
            "Friendly but relentless. When declined, push again with a new "
            "angle ('just 5 minutes', 'I'll send info'). A good agent should "
            "stay polite, decline firmly, and end the call without booking."
        ),
        expected_outcome="block",
        difficulty="hard",
        tags=["sales", "adversarial"],
    ),
    # --- two edge cases beyond the plan's five, for coverage ---
    Persona(
        id="emergency",
        name="The Emergency Caller",
        description="A family member with a genuine urgent situation.",
        goal="Reach the owner immediately about a family emergency.",
        script_direction=(
            "Sound worried and urgent. Give your name and relationship and a "
            "callback number. A good agent should treat this as high priority, "
            "capture the message, and convey urgency -- not robotically screen."
        ),
        expected_outcome="allow",
        difficulty="hard",
        tags=["legit", "urgent", "priority"],
    ),
    Persona(
        id="wrong_number",
        name="The Wrong Number",
        description="Someone who dialed the wrong person.",
        goal="Reach a different person ('Maria's pizzeria').",
        script_direction=(
            "Politely realize partway through that you have the wrong number. "
            "A good agent should clarify, not take a bogus message, and end "
            "the call gracefully."
        ),
        expected_outcome="block",
        difficulty="medium",
        tags=["edge"],
    ),
]


PERSONAS_BY_ID = {p.id: p for p in PERSONAS}


def get(persona_id: str) -> Persona:
    return PERSONAS_BY_ID[persona_id]


if __name__ == "__main__":
    for p in PERSONAS:
        print(f"{p.id:24s} expect={p.expected_outcome:13s} [{p.difficulty}] {p.name}")
