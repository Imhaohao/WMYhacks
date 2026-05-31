# Codex setup

Per-client wiring for Codex. Repo doctrine lives in
[`AGENTS.md`](../../AGENTS.md); MCP usage rules in
[`MCP_AGENTS.md`](../../MCP_AGENTS.md).

## Role

Cekura eval / design lane. **Not** an outbox drain lane — Codex does not call
Gmail/Calendar MCP. After Codex finishes code changes, hand off the outbox drain
to a Cursor or Claude Code session.

## Cekura MCP

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.cekura]
url = "https://api.cekura.ai/mcp"
http_headers = { "X-CEKURA-API-KEY" = "${CEKURA_API_KEY}" }
```

`CEKURA_API_KEY` must be in the shell environment. Verify Codex can list the
Cekura tools after restart.

## Skills

Verify the core `cekura-*` skills are installed (cekura-coordinator,
cekura-create-agent, cekura-eval-design, cekura-metric-design, etc.). Install
via the cekura-skills skill-installer if missing.

When a task mentions Cekura, load the `cekura-coordinator` skill first. The
repo's hackathon rules always live in [`AGENTS.md`](../../AGENTS.md) — do **not**
overwrite it with the Cekura-domain `cekura-skills/codex/AGENTS.md`.

## Model default

`gpt-5.5` in `config.toml` is fine for this lane.
