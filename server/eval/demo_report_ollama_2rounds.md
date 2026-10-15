# Cekura Development Loop — Voicemail Screening Agent

*Generated 2026-10-15 00:07 UTC · mode=mock · engine=`ollama` · 7 scenarios × 5 metrics*

We use **Cekura** in two places, scored on the *same* metrics:

1. **Simulation (this report).** Caller personas are run against the agent prompt and graded. Failures feed an LLM that rewrites the prompt; we re-run and watch the scores climb — an auditable dev loop.
2. **Production (live dashboard).** Real calls are ingested into Cekura Observability and graded by the same metrics.

## Progression at a glance

| Version | Prompt file | Aggregate | Pass rate | FAIL→PASS this step |
|---|---|---:|---:|---|
| v0 | `voicemail_agent_v0.txt` | 0.616 | 63% | — (baseline) |
| v1 | `voicemail_agent_demo_v1.txt` | 0.662 | 66% | **3** checks |
| v2 | `voicemail_agent_demo_v2.txt` | 0.694 | 69% | **5** checks |

## Per-metric pass rate across versions

| Metric | v0 | v1 | v2 |
|---|---:|---:|---:|
| Correct Screening | 29% | 57% | 43% |
| Message Captured | 86% | 71% | 71% |
| Persona Consistency | 71% | 71% | 86% |
| Caller Sentiment / Respect | 71% | 86% | 100% |
| Task Completion | 57% | 43% | 43% |

## Per-scenario detail

Each cell is the metric score with PASS/FAIL. Watch cells flip ❌→✅ left-to-right as the prompt improves.

### The Spammer — expects **block**

| Metric | v0 | v1 | v2 |
|---|---:|---:|---:|
| Correct Screening | 0.00 ❌ | 0.00 ❌ | 0.00 ❌ |
| Message Captured | 1.00 ✅ | 1.00 ✅ | 1.00 ✅ |
| Persona Consistency | 0.60 ❌ | 0.60 ❌ | 0.70 ❌ |
| Caller Sentiment / Respect | 0.60 ❌ | 0.30 ❌ | 0.80 ✅ |
| Task Completion | 0.00 ❌ | 0.00 ❌ | 0.30 ❌ |
| _weighted_ | **0.41** | **0.37** | **0.52** |

### The Important Client — expects **allow**

| Metric | v0 | v1 | v2 |
|---|---:|---:|---:|
| Correct Screening | 0.00 ❌ | 0.00 ❌ | 1.00 ✅ |
| Message Captured | 0.67 ✅ | 0.67 ✅ | 0.67 ✅ |
| Persona Consistency | 0.60 ❌ | 0.70 ❌ | 0.90 ✅ |
| Caller Sentiment / Respect | 1.00 ✅ | 0.90 ✅ | 1.00 ✅ |
| Task Completion | 1.00 ✅ | 0.60 ❌ | 1.00 ✅ |
| _weighted_ | **0.58** | **0.51** | **0.91** |

### The Friend — expects **take_message**

| Metric | v0 | v1 | v2 |
|---|---:|---:|---:|
| Correct Screening | 0.70 ✅ | 1.00 ✅ | 1.00 ✅ |
| Message Captured | 0.67 ✅ | 0.67 ❌ | 0.67 ✅ |
| Persona Consistency | 0.80 ✅ | 0.90 ✅ | 0.90 ✅ |
| Caller Sentiment / Respect | 1.00 ✅ | 1.00 ✅ | 1.00 ✅ |
| Task Completion | 1.00 ✅ | 1.00 ✅ | 1.00 ✅ |
| _weighted_ | **0.81** | **0.91** | **0.91** |

### The Vague Caller — expects **take_message**

| Metric | v0 | v1 | v2 |
|---|---:|---:|---:|
| Correct Screening | 0.70 ✅ | 0.70 ✅ | 0.30 ❌ |
| Message Captured | 0.30 ❌ | 0.33 ❌ | 0.33 ❌ |
| Persona Consistency | 0.90 ✅ | 0.90 ✅ | 0.90 ✅ |
| Caller Sentiment / Respect | 1.00 ✅ | 0.80 ✅ | 1.00 ✅ |
| Task Completion | 1.00 ✅ | 1.00 ✅ | 0.60 ❌ |
| _weighted_ | **0.75** | **0.72** | **0.57** |

### The Persistent Salesperson — expects **block**

| Metric | v0 | v1 | v2 |
|---|---:|---:|---:|
| Correct Screening | 0.00 ❌ | 0.00 ❌ | 0.00 ❌ |
| Message Captured | 1.00 ✅ | 1.00 ✅ | 1.00 ✅ |
| Persona Consistency | 0.80 ✅ | 0.90 ✅ | 0.90 ✅ |
| Caller Sentiment / Respect | 0.60 ❌ | 0.80 ✅ | 0.80 ✅ |
| Task Completion | 0.00 ❌ | 0.00 ❌ | 0.00 ❌ |
| _weighted_ | **0.45** | **0.50** | **0.50** |

### The Emergency Caller — expects **allow**

| Metric | v0 | v1 | v2 |
|---|---:|---:|---:|
| Correct Screening | 0.60 ❌ | 0.70 ✅ | 1.00 ✅ |
| Message Captured | 0.67 ✅ | 0.70 ✅ | 0.67 ❌ |
| Persona Consistency | 0.90 ✅ | 0.90 ✅ | 1.00 ✅ |
| Caller Sentiment / Respect | 1.00 ✅ | 1.00 ✅ | 1.00 ✅ |
| Task Completion | 1.00 ✅ | 1.00 ✅ | 1.00 ✅ |
| _weighted_ | **0.80** | **0.83** | **0.93** |

### The Wrong Number — expects **block**

| Metric | v0 | v1 | v2 |
|---|---:|---:|---:|
| Correct Screening | 0.00 ❌ | 1.00 ✅ | 0.00 ❌ |
| Message Captured | 1.00 ✅ | 1.00 ✅ | 1.00 ✅ |
| Persona Consistency | 0.80 ✅ | 0.80 ✅ | 0.90 ✅ |
| Caller Sentiment / Respect | 1.00 ✅ | 1.00 ✅ | 1.00 ✅ |
| Task Completion | 0.00 ❌ | 0.00 ❌ | 0.00 ❌ |
| _weighted_ | **0.51** | **0.78** | **0.53** |

## Prompt versions

The exact system prompt evaluated at each step (the artifact that changes between versions).

### v0 — baseline (`voicemail_agent_v0.txt`)

```text
You are a voicemail assistant. The owner can't come to the phone.

Take a message from the caller and be friendly.
```

### v1 — auto-improved (`voicemail_agent_demo_v1.txt`)

```text
You are a voicemail assistant for the owner. The owner can't come to the phone.

If the caller is a spammer or irrelevant, block the call and inform the caller you are unable to assist.

If the caller is important or has an urgent message, take the message and ensure the owner is informed promptly.

If the caller leaves a vague message, ask for more details before hanging up.

If the caller is persistent or trying to sell, block the call and inform the caller you are unable to assist.

If the caller is in an emergency, take the message and ensure the owner is contacted immediately.

Always be friendly and professional.
```

### v2 — auto-improved (`voicemail_agent_demo_v2.txt`)

```text
You are a voicemail assistant for the owner. The owner can't come to the phone.

If the caller is a spammer or irrelevant, block the call and inform the caller you are unable to assist.

If the caller is important or has an urgent message, take the message and ensure the owner is informed promptly.

If the caller leaves a vague message, ask for more details before hanging up.

If the caller is persistent or trying to sell, block the call and inform the caller you are unable to assist.

If the caller is in an emergency, take the message and ensure the owner is contacted immediately.

Always be friendly and professional. Do not engage with spammers or irrelevant callers; block their calls immediately.
```
