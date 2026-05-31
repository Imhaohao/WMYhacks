"""Derive local Git workflow habits for this repository.

Git history is inspected read-only. Commit subjects, reflog messages, and
branch names are reduced to counts and categories locally; raw metadata is
never sent to the summarizer or written to persona_context.md.
"""

from __future__ import annotations

import re
import statistics
import subprocess
from collections import Counter
from pathlib import Path

from . import persona_sections as ps
from .local_llm import summarize_to_bullets

DEFAULT_REPO = Path(__file__).resolve().parents[2]
_COMMIT_KINDS = ("add", "build", "chore", "docs", "feat", "fix", "merge", "refactor", "test")
_REFLOG_ACTIONS = ("checkout", "clone", "commit", "merge", "pull", "rebase", "reset")


def _git(repo: Path, *args: str) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git command failed")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def read_signals(repo: Path = DEFAULT_REPO, max_items: int = 100) -> dict[str, list[str]]:
    """Return raw local metadata for immediate in-process aggregation only."""
    return {
        "commits": _git(repo, "log", f"-n{max_items}", "--format=%s"),
        "reflog": _git(repo, "reflog", f"-n{max_items}", "--format=%gs"),
        "branches": _git(repo, "branch", "--format=%(refname:short)"),
    }


def _kind_counts(lines: list[str], kinds: tuple[str, ...]) -> Counter:
    counts: Counter = Counter()
    for line in lines:
        lower = line.lower()
        for kind in kinds:
            if re.search(rf"\b{re.escape(kind)}\b", lower):
                counts[kind] += 1
                break
    return counts


def _derive_facts(signals: dict[str, list[str]]) -> tuple[str, list[str]]:
    commits = signals["commits"]
    reflog = signals["reflog"]
    branches = signals["branches"]
    lengths = [len(subject.split()) for subject in commits]
    typical = statistics.median(lengths) if lengths else 0
    style = "concise" if typical < 8 else "descriptive"
    commit_kinds = _kind_counts(commits, _COMMIT_KINDS)
    reflog_actions = _kind_counts(reflog, _REFLOG_ACTIONS)
    kind_text = ", ".join(f"{kind}={count}" for kind, count in commit_kinds.most_common()) or "none"
    action_text = ", ".join(f"{kind}={count}" for kind, count in reflog_actions.most_common()) or "none"
    facts = (
        f"recent commit count: {len(commits)}\n"
        f"typical commit subject length: {typical:.0f} words ({style})\n"
        f"commit categories: {kind_text}\n"
        f"reflog action categories: {action_text}\n"
        f"local branch count: {len(branches)}"
    )
    fallback = [
        f"- Uses {style} Git commit subjects (~{typical:.0f} words, median).",
        f"- Recent Git workflow includes: {action_text}.",
        f"- Maintains {len(branches)} local branch(es) in this repo.",
    ]
    return facts, fallback


def ingest(*, repo: Path = DEFAULT_REPO, dry_run: bool = False) -> dict:
    report: dict = {"source": "git", "status": "skipped", "blocks": []}
    if not repo.exists():
        report["reason"] = f"repo not found at {repo}"
        return report
    try:
        signals = read_signals(repo)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        report["reason"] = f"cannot read local Git metadata ({exc})"
        return report
    if not signals["commits"] and not signals["reflog"]:
        report["reason"] = "no local Git history found"
        report["status"] = "ok"
        return report

    facts, fallback = _derive_facts(signals)
    res = summarize_to_bullets(
        facts,
        "Describe this person's Git working habits. Keep it neutral and useful "
        "for an assistant. Do not infer project details.",
        fallback,
        strict_local=True,
        max_items=3,
    )
    changed, _ = ps.update_file("Recent Agent Context", "git", res.lines, dry_run=dry_run)
    report["blocks"].append(
        {"section": "Recent Agent Context", "block_id": "git", "lines": res.lines, "changed": changed}
    )
    report["status"] = "ok"
    report["engine"] = res.engine
    report["stats"] = {
        "commits_seen": len(signals["commits"]),
        "reflog_entries_seen": len(signals["reflog"]),
        "branches_seen": len(signals["branches"]),
    }
    return report
