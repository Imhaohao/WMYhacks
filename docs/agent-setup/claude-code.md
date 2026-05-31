# Claude Code setup

Per-client wiring for Claude Code. Repo doctrine lives in
[`AGENTS.md`](../../AGENTS.md); MCP usage rules in
[`MCP_AGENTS.md`](../../MCP_AGENTS.md).

## Role

Full stack — Cekura MCP + slash commands, and a valid **outbox drain lane**
(can call Gmail/Calendar MCP after `action_bridge.py --emit`).

## Cekura (MCP + skills)

Install the marketplace plugin (bundles skills, slash commands, auto-configured
MCP — see [README §Test your agent with Cekura](../../README.md)):

```
/plugin marketplace add cekura-ai/cekura-skills
/plugin install cekura@cekura-skills
```

Then connect the MCP and confirm tools:

1. Run `/setup-mcp` (OAuth recommended).
2. Verify `mcp__cekura__list_available_tools` succeeds.
3. When connecting the agent in Cekura, select **Pipecat** as the provider.

Useful slash commands: `/cekura-report` (spins up 10–20 evaluators against the
Pipecat bot). Full guide: <https://docs.cekura.ai/mcp/claude-code-guide>.

## Model default

Set `~/.claude/settings.json` `model` to **Sonnet** for implementation loops.
Open Opus only for explicit routing / planning / ambiguous-architecture threads
(matches the delegation table in [`AGENTS.md`](../../AGENTS.md)).

## Outbox drain

Follow the SOP in [`MCP_AGENTS.md` §3](../../MCP_AGENTS.md): `--list` → `--emit`
→ execute MCP call → `--done <action_id>`.
