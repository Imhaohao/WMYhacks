"""Extract privacy-bounded recurring lingo from owner-authored text.

The output is intentionally small: repeated one-to-three-token snippets and a
few style notes. Raw messages never leave the caller, and one-off text is
discarded so names, topics, and accidental secrets do not become persona data.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9']{0,19}")
_SENSITIVE = re.compile(
    r"https?://\S+|www\.\S+|"
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|"
    r"\+?\d[\d(). -]{6,}\d|"
    r"\b(?:sk|ghp|AIza|ya29|1//)[A-Za-z0-9_./+-]{8,}\b|"
    r"(?:^|\s)/(?:Users|home|tmp|var|private|opt)/\S+",
    re.IGNORECASE,
)
_SECRET_WORDS = {
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}
_COMMON_ACRONYMS = {
    "AI",
    "API",
    "ASR",
    "AWS",
    "CLI",
    "DB",
    "GPT",
    "HTTP",
    "IAM",
    "JSON",
    "LLM",
    "MCP",
    "PDF",
    "PR",
    "SMS",
    "SQL",
    "SSH",
    "STT",
    "TTS",
    "URL",
    "UX",
}
_SLANG = {
    "bet",
    "bro",
    "bruh",
    "dude",
    "fam",
    "fr",
    "highkey",
    "idk",
    "imo",
    "lmao",
    "lol",
    "lowkey",
    "nah",
    "ngl",
    "tbh",
    "yap",
    "yo",
}
_KNOWN_PHRASES = {
    "for real",
    "low key",
    "not bad",
    "not crazy",
    "not impossible",
    "not really",
    "not terrible",
    "not too bad",
    "per chance",
    "yo bro",
}
_BORING_PHRASES = {
    "can you",
    "could you",
    "do you",
    "how do",
    "i need",
    "i think",
    "i want",
    "in the",
    "is there",
    "it is",
    "make sure",
    "of the",
    "please make",
    "thank you",
    "that is",
    "this is",
    "to the",
    "with the",
}
_LITOTES = re.compile(
    r"\bnot\s+(?:too\s+)?(?:awful|bad|crazy|impossible|really|terrible|uncommon|unlikely|wrong)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LingoProfile:
    """A compact derived voice profile safe to write into persona context."""

    phrases: list[str]
    style_notes: list[str]


def _tokens(text: str) -> list[tuple[str, str]]:
    sanitized = _SENSITIVE.sub(" ", text)
    tokens = []
    for raw in _TOKEN.findall(sanitized):
        normalized = raw.lower()
        if normalized in _SECRET_WORDS:
            continue
        tokens.append((raw, normalized))
    return tokens


def _display(words: tuple[tuple[str, str], ...]) -> str:
    return " ".join(raw if raw.isupper() and 2 <= len(raw) <= 8 else normalized for raw, normalized in words)


def _is_acronym(raw: str) -> bool:
    return raw.isalpha() and raw.isupper() and 2 <= len(raw) <= 8 and raw not in _COMMON_ACRONYMS


def _candidate(
    words: tuple[tuple[str, str], ...],
    *,
    allow_acronyms: bool,
    allow_generic: bool,
) -> bool:
    normalized = " ".join(word[1] for word in words)
    if normalized in _BORING_PHRASES:
        return False
    if any(word[1] in _SECRET_WORDS for word in words):
        return False
    if len(words) == 1:
        raw, lowered = words[0]
        return lowered in _SLANG or (allow_acronyms and _is_acronym(raw))
    if normalized in _KNOWN_PHRASES or _LITOTES.fullmatch(normalized):
        return True
    if any(
        word[1] in _SLANG or (allow_acronyms and _is_acronym(word[0]))
        for word in words
    ):
        return all(word[1] in _SLANG for word in words)
    return allow_generic and all(len(word[1]) >= 3 for word in words)


def derive_lingo(
    texts: list[str],
    *,
    min_messages: int = 2,
    generic_min_messages: int = 3,
    max_phrases: int = 6,
    allow_acronyms: bool = True,
    allow_generic: bool = True,
) -> LingoProfile:
    """Return repeated short phrases and derived style notes.

    Counts are document-frequency based: repeating a phrase many times in one
    long prompt is not enough. Generic n-grams need a higher threshold than
    slang, acronyms, and recognized understatement patterns.
    """
    seen_in: dict[str, set[int]] = defaultdict(set)
    score_boost: Counter[str] = Counter()
    litotes_messages = 0

    for index, text in enumerate(texts):
        words = _tokens(text)
        normalized_text = " ".join(word[1] for word in words)
        if _LITOTES.search(normalized_text):
            litotes_messages += 1
        for size in (1, 2, 3):
            for start in range(0, len(words) - size + 1):
                phrase_words = tuple(words[start : start + size])
                display = _display(phrase_words)
                normalized = display.lower()
                if not _candidate(
                    phrase_words,
                    allow_acronyms=allow_acronyms,
                    allow_generic=allow_generic and size > 1,
                ):
                    continue
                seen_in[display].add(index)
                if normalized in _KNOWN_PHRASES or _LITOTES.fullmatch(normalized):
                    score_boost[display] += 6
                elif any(
                    word[1] in _SLANG or (allow_acronyms and _is_acronym(word[0]))
                    for word in phrase_words
                ):
                    score_boost[display] += 3

    ranked = []
    for phrase, indexes in seen_in.items():
        count = len(indexes)
        is_generic = score_boost[phrase] == 0
        threshold = generic_min_messages if is_generic else min_messages
        if count < threshold:
            continue
        ranked.append((score_boost[phrase], len(phrase.split()), count, phrase))
    ranked.sort(reverse=True)

    phrases: list[str] = []
    for _, _, _, phrase in ranked:
        lowered = phrase.lower()
        if any(lowered in selected.lower() or selected.lower() in lowered for selected in phrases):
            continue
        phrases.append(phrase)
        if len(phrases) >= max_phrases:
            break

    notes = []
    if litotes_messages >= min_messages:
        notes.append("Uses understated negation or litotes when it fits.")
    return LingoProfile(phrases=phrases, style_notes=notes)


def render_lingo_lines(profile: LingoProfile, source_label: str) -> list[str]:
    """Render a Persona-section block from a derived lingo profile."""
    lines = []
    if profile.phrases:
        quoted = ", ".join(f'"{phrase}"' for phrase in profile.phrases)
        lines.append(
            f"- {source_label} lingo: naturally and sparingly uses {quoted}; "
            "match the caller and setting instead of forcing slang."
        )
    lines.extend(f"- {note}" for note in profile.style_notes)
    return lines
