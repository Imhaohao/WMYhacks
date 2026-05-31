"""Best-effort local ChatGPT memo ingestion with defensive format checks.

ChatGPT desktop stores conversation files below Application Support. Some app
versions use opaque binary `.data` files. Those are deliberately skipped: this
adapter parses only UTF-8 JSON or JSONL with explicit user-role messages. Raw
prompts are reduced locally to aggregate facts before derived bullets are
written to persona_context.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import memo_context
from . import persona_sections as ps
from .local_llm import summarize_to_bullets

DEFAULT_ROOT = Path.home() / "Library" / "Application Support" / "com.openai.chat"


def _extract_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [_extract_content(item) for item in content]
        return " ".join(part for part in parts if part)
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if "parts" in content:
            return _extract_content(content["parts"])
        if "content" in content:
            return _extract_content(content["content"])
    return ""


def _iter_user_prompts(node):
    if isinstance(node, list):
        for item in node:
            yield from _iter_user_prompts(item)
        return
    if not isinstance(node, dict):
        return

    author = node.get("author")
    author_role = author.get("role") if isinstance(author, dict) else None
    role = node.get("role") or author_role
    if role == "user":
        text = _extract_content(node.get("content")).strip()
        if text:
            yield text
        return

    for value in node.values():
        yield from _iter_user_prompts(value)


def _load_json(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        records = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                return None
        return records or None


def _conversation_files(root: Path) -> list[Path]:
    try:
        nested = [
            path
            for path in root.glob("**/conversations-v3-*/*")
            if path.is_file() and path.suffix.lower() in {".data", ".json", ".jsonl"}
        ]
        direct = [
            path
            for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in {".data", ".json", ".jsonl"}
        ]
        return sorted(set(nested + direct), key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return []


def read_prompts(
    root: Path = DEFAULT_ROOT,
    max_files: int = 100,
    max_prompts: int = 500,
) -> tuple[list[str], int]:
    """Return owner prompts and the number of safely decoded conversation files."""
    prompts: list[str] = []
    decoded = 0
    for path in _conversation_files(root)[:max_files]:
        obj = _load_json(path)
        if obj is None:
            continue
        decoded += 1
        for text in _iter_user_prompts(obj):
            prompts.append(text)
            if len(prompts) >= max_prompts:
                return prompts, decoded
    return prompts, decoded


def ingest(*, root: Path = DEFAULT_ROOT, dry_run: bool = False) -> dict:
    report: dict = {"source": "chatgpt", "status": "skipped", "blocks": []}
    if not root.exists():
        report["reason"] = f"ChatGPT desktop data not found at {root}"
        return report
    files = _conversation_files(root)
    if not files:
        report["reason"] = "no local ChatGPT conversation files found"
        return report

    prompts, decoded = read_prompts(root)
    if decoded == 0:
        report["reason"] = "local ChatGPT conversation files use an unsupported binary format"
        return report
    if not prompts:
        report["reason"] = "no owner prompts found in decoded ChatGPT conversations"
        report["status"] = "ok"
        return report

    facts, fallback = memo_context.derive_prompt_facts(prompts)
    res = summarize_to_bullets(
        facts,
        "Describe how this person prefers to work and communicate. Focus on "
        "brevity, directness, and workflow habits. Keep it to a few bullets.",
        fallback,
        strict_local=True,
        max_items=3,
    )
    changed, _ = ps.update_file("Recent Agent Context", "chatgpt", res.lines, dry_run=dry_run)
    report["blocks"].append(
        {"section": "Recent Agent Context", "block_id": "chatgpt", "lines": res.lines, "changed": changed}
    )
    lingo_lines = memo_context.derive_prompt_lingo_lines(prompts, "ChatGPT prompt")
    changed, _ = ps.update_optional_file("Persona", "chatgpt_lingo", lingo_lines, dry_run=dry_run)
    report["blocks"].append(
        {
            "section": "Persona",
            "block_id": "chatgpt_lingo",
            "lines": lingo_lines,
            "changed": changed,
        }
    )
    report["status"] = "ok"
    report["engine"] = res.engine
    report["stats"] = {"prompts_seen": len(prompts), "files_decoded": decoded}
    return report
