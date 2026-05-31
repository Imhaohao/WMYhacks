"""Derive the owner's writing voice from sent Gmail messages.

Raw email bodies stay in memory and are reduced to aggregate style signals
before summarization. Only derived bullets are written to persona_context.md.
"""

from __future__ import annotations

import statistics

import google_gmail

from . import persona_sections as ps
from .local_llm import summarize_to_bullets
from .voice_lingo import derive_lingo, render_lingo_lines


def _style_facts(bodies: list[str]) -> tuple[list[str], list[str]]:
    """Return aggregate sent-mail style facts and deterministic bullets."""
    if not bodies:
        return [], []

    lengths = [len(body.split()) for body in bodies]
    typical = statistics.median(lengths)
    brevity = "concise" if typical < 45 else "moderate" if typical < 120 else "detailed"
    count = len(bodies)

    def ratio(predicate) -> float:
        return sum(1 for body in bodies if predicate(body)) / count

    greeting_rate = ratio(
        lambda body: body.lstrip().lower().startswith(("hi ", "hello ", "hey ", "dear "))
    )
    signoff_rate = ratio(
        lambda body: any(
            marker in body.lower()
            for marker in ("\nthanks", "\nbest", "\nregards", "\ncheers", "\nthank you")
        )
    )
    question_rate = ratio(lambda body: "?" in body)
    exclamation_rate = ratio(lambda body: "!" in body)

    facts = [
        f"sent emails sampled: {count}",
        f"typical sent-email length: {typical:.0f} words ({brevity})",
        f"greeting rate: {greeting_rate:.0%}",
        f"signoff rate: {signoff_rate:.0%}",
        f"question-mark rate: {question_rate:.0%}",
        f"exclamation-mark rate: {exclamation_rate:.0%}",
    ]
    fallback = [
        f"- Mirrors a {brevity} email style (~{typical:.0f} words per sent message, median).",
        (
            "- Uses a warm email style with greetings and signoffs."
            if greeting_rate >= 0.4 or signoff_rate >= 0.4
            else "- Uses a direct email style with minimal ceremony."
        ),
    ]
    return facts, fallback


def ingest(*, max_messages: int = 100, dry_run: bool = False) -> dict:
    """Read sent Gmail messages and write a derived Persona style block."""
    report: dict = {"source": "gmail", "status": "skipped", "blocks": []}
    if not google_gmail.is_connected():
        report["reason"] = "Connect or reconnect Gmail to authorize sent-mail style learning"
        return report

    bodies = google_gmail.read_sent_bodies(max_messages=max_messages)
    if not bodies:
        report["reason"] = "no readable sent Gmail messages found"
        return report

    facts, fallback = _style_facts(bodies)
    res = summarize_to_bullets(
        "\n".join(facts),
        "Describe how this person writes email so a personal voice assistant can "
        "mirror their tone. Focus on brevity, warmth, and directness. Keep it to "
        "a few bullets.",
        fallback,
        strict_local=True,
        max_items=3,
    )
    changed, _ = ps.update_file("Persona", "gmail_style", res.lines, dry_run=dry_run)
    report["blocks"].append(
        {"section": "Persona", "block_id": "gmail_style", "lines": res.lines, "changed": changed}
    )
    lingo_lines = render_lingo_lines(derive_lingo(bodies), "Email")
    changed, _ = ps.update_optional_file("Persona", "gmail_lingo", lingo_lines, dry_run=dry_run)
    report["blocks"].append(
        {
            "section": "Persona",
            "block_id": "gmail_lingo",
            "lines": lingo_lines,
            "changed": changed,
        }
    )
    report["status"] = "ok"
    report["engine"] = res.engine
    report["stats"] = {"sent_messages_seen": len(bodies)}
    return report
