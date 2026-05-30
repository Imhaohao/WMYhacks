"""Live owner-intelligence ingestion (Person 2, Part B).

Derives summaries from the owner's real data — recent iMessages, Google
Calendar availability, and past Claude sessions — and writes them into
`persona_context.md`, the same file Part A's `load_persona_context()` loader
already feeds into the bot's system instruction. No fork of the bot, no new
runtime dependency: the bot just reads a richer file.

Privacy is load-bearing:
- Only DERIVED summaries are written to disk — never raw message bodies.
- iMessage content is summarized STRICT-LOCAL (local Ollama or a deterministic
  extractor), never a cloud model.
- `persona_context.py` appends a privacy guard so the bot treats everything as
  reasoning-only context it must never recite.

One command refreshes everything:  `uv run python -m ingest.refresh`
"""
