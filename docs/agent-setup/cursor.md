# Cursor setup

Per-client wiring for Cursor. Repo doctrine lives in
[`AGENTS.md`](../../AGENTS.md); MCP usage rules in
[`MCP_AGENTS.md`](../../MCP_AGENTS.md).

## Role

**Primary MCP drain lane** + hackathon dev. Exposes Cekura, Gmail, and Calendar
MCP so it can drain the bot outbox end-to-end.

## Project MCP

[`.cursor/mcp.json`](../../.cursor/mcp.json) declares the Cekura server with an
env reference (no secrets in git). `CEKURA_API_KEY` must be set in the shell
that launches Cursor (or in `server/.env` loaded into the environment).

## Rules

- [`.cursor/rules/mcp-bridge.mdc`](../../.cursor/rules/mcp-bridge.mdc) — always-on:
  read `MCP_AGENTS.md` before connector calls; drain outbox after a WebRTC bot
  test; calendar events use the `[Tentative]` title convention; prefer Nia over
  WebFetch for indexed docs.
- [`.cursor/rules/hackathon.mdc`](../../.cursor/rules/hackathon.mdc) — pointer to
  the `AGENTS.md` delegation table (Sonnet for `P3_NEXT.md` tasks).

## Enable Gmail + Calendar MCP (user OAuth, not in git)

In **Cursor Settings → MCP**, connect Gmail and Google Calendar via OAuth.
Reconnect Calendar with **write** scope (read-only is the current blocker noted
in [`server/INTEGRATION_P3.md`](../../server/INTEGRATION_P3.md)).

## Secrets hygiene

Move the Nia key in `~/.cursor/mcp.json` from plaintext to a `${NIA_API_KEY}`
env reference, and rotate it if the file was ever shared.

## Optional

Enable the `claude-mem` plugin for cross-session memory (already in the
workspace MCP cache).

## Outbox drain

Follow [`MCP_AGENTS.md` §3](../../MCP_AGENTS.md): `--list` → `--emit` → execute
MCP call → `--done <action_id>`.
