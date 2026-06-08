"""Hermetic tests for privacy-bounded owner lingo extraction."""

from __future__ import annotations

from ingest.memo_context import derive_prompt_lingo_lines
from ingest.voice_lingo import derive_lingo, derive_onboarding_lingo, render_lingo_lines


def test_extracts_repeated_slang_acronym_and_litotes():
    profile = derive_lingo(
        [
            "yo bro, EBK. honestly that is not bad",
            "yo bro EBK, not bad at all",
            "yo bro - EBK and not bad",
        ]
    )

    assert "yo bro" in profile.phrases
    assert "EBK" in profile.phrases
    assert "not bad" in profile.phrases
    assert "Uses understated negation or litotes when it fits." in profile.style_notes


def test_drops_one_off_phrases():
    profile = derive_lingo(["yo bro", "totally unrelated", "a third message"])
    assert profile.phrases == []


def test_filters_sensitive_values_before_rendering():
    texts = [
        "yo bro email jane@example.com token sk-secretvalue123456 call +1 (555) 123-4567",
        "yo bro email jane@example.com token sk-secretvalue123456 call +1 (555) 123-4567",
        "yo bro email jane@example.com token sk-secretvalue123456 call +1 (555) 123-4567",
    ]
    rendered = " ".join(render_lingo_lines(derive_lingo(texts), "Message"))

    assert "yo bro" in rendered
    assert "jane@example.com" not in rendered
    assert "sk-secretvalue123456" not in rendered
    assert "555" not in rendered


def test_render_reminds_agent_not_to_force_slang():
    rendered = render_lingo_lines(derive_lingo(["per chance EBK", "per chance EBK"]), "Prompt")
    assert rendered
    assert "naturally and sparingly" in rendered[0]
    assert "instead of forcing slang" in rendered[0]


def test_prompt_lingo_drops_repeated_technical_vocabulary():
    lines = derive_prompt_lingo_lines(
        [
            "README XML SMTP yo bro not bad",
            "README XML SMTP yo bro not bad",
            "README XML SMTP yo bro not bad",
        ],
        "Prompt",
    )
    rendered = " ".join(lines)
    assert "yo bro" in rendered and "not bad" in rendered
    assert "README" not in rendered and "XML" not in rendered and "SMTP" not in rendered


def test_onboarding_lingo_combines_safe_explicit_phrases_and_style_notes():
    profile = derive_onboarding_lingo(
        [
            "gotchu, i'll take a look",
            "gotchu, i'll circle back",
            "gotchu, talk soon",
        ],
        explicit_phrases=["gotchu", "talk soon", "jane@example.com", "sk-secretvalue123456"],
    )

    assert "talk soon" in profile.phrases
    assert "gotchu" in profile.phrases
    assert "jane@example.com" not in " ".join(profile.phrases)
    assert "sk-secretvalue123456" not in " ".join(profile.phrases)
    assert "Uses casual contractions and relaxed spoken phrasing." in profile.style_notes
    assert "Prefers short, direct replies." in profile.style_notes
