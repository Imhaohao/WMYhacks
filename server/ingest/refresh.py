"""Refresh persona_context.md from the owner's live data (Part B entry point).

    uv run python -m ingest.refresh                  # all sources
    uv run python -m ingest.refresh --dry-run        # show, don't write
    uv run python -m ingest.refresh --sources calendar,agent
    uv run python -m ingest.refresh --days 7

Each source degrades independently: a missing permission, model, or credential
skips that source with a clear reason and never crashes the run. Only derived
summaries are written; iMessage stays strict-local.
"""

from __future__ import annotations

import argparse

from . import agent_context, gcal, imessage, local_llm

SOURCES = ("imessage", "calendar", "agent")


def _run_source(name: str, days: int | None, dry_run: bool) -> dict:
    if name == "imessage":
        return imessage.ingest(days=days or 14, dry_run=dry_run)
    if name == "calendar":
        return gcal.ingest(days=days or 7, dry_run=dry_run)
    if name == "agent":
        return agent_context.ingest(dry_run=dry_run)
    return {"source": name, "status": "skipped", "reason": "unknown source", "blocks": []}


def _print_report(rep: dict) -> None:
    src = rep["source"]
    if rep["status"] != "ok":
        print(f"  ⚠ {src}: skipped — {rep.get('reason', 'unknown')}")
        return
    engine = rep.get("engine", "n/a")
    stats = rep.get("stats", {})
    print(f"  ✓ {src}: ok  (engine={engine}, {stats})")
    for blk in rep.get("blocks", []):
        flag = "updated" if blk["changed"] else "unchanged"
        print(f"      [{blk['section']}] {flag}:")
        for line in blk["lines"]:
            print(f"        {line}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Refresh persona_context.md from live owner data.")
    ap.add_argument("--sources", default=",".join(SOURCES),
                    help=f"comma-separated subset of {SOURCES}")
    ap.add_argument("--days", type=int, default=None, help="lookback window (per-source default if omitted)")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, but don't write files")
    args = ap.parse_args(argv)

    requested = [s.strip() for s in args.sources.split(",") if s.strip()]
    unknown = [s for s in requested if s not in SOURCES]
    if unknown:
        ap.error(f"unknown source(s): {unknown}. choose from {SOURCES}")

    mode = "DRY RUN (no writes)" if args.dry_run else "writing persona_context.md"
    print(f"persona refresh — {mode}")
    engine = local_llm.probe_engine()
    print(f"summarizer engine available: {engine}")
    if engine == "ollama":
        print("  (the first model call loads it into memory — 20–40s is normal; "
              "set OLLAMA_MODEL=llama3.2:3b for a faster, lighter model)")
    print(f"sources: {', '.join(requested)}\n")

    reports = []
    for name in requested:
        print(f"→ {name} … (summarizing locally, please wait)", flush=True)
        rep = _run_source(name, args.days, args.dry_run)
        reports.append(rep)
        _print_report(rep)

    ok = sum(r["status"] == "ok" for r in reports)
    print(f"\ndone: {ok}/{len(reports)} sources ok.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
