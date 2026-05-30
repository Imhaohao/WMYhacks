"""Cekura eval harness for the voicemail persona agent (Person 2).

Modules:
- personas:  caller personas used to simulate inbound calls
- metrics:   scoring metrics (LLM-as-judge) that define "good"
- simulate:  local caller<->agent conversation simulator (mock mode)
- judge:     LLM-as-judge scoring of transcripts against metrics
- run_evals: orchestrator that produces a scorecard
- improve:   the auto-improvement loop (eval -> rewrite prompt -> re-eval)
- cekura_client: persona/metric -> Cekura payload mappings + MCP provisioning plan
- llm:       LLM engine abstraction (Ollama -> OpenAI -> offline stub)
"""
