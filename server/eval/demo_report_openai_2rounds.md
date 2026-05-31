# Cekura Development Loop — Voicemail Screening Agent

_Generated 2026-05-30 23:26 UTC · mode=mock · engine=`openai` · 7 scenarios × 5 metrics_

**Simulation.** Caller personas are run against the agent prompt and graded. Failures feed an LLM that rewrites the prompt; we re-run and watch the scores climb.

## Progression at a glance

| Version | Prompt file                   | Aggregate | Pass rate | FAIL→PASS this step |
| ------- | ----------------------------- | --------: | --------: | ------------------- |
| v0      | `voicemail_agent_v0.txt`      |     0.720 |       71% | — (baseline)        |
| v1      | `voicemail_agent_demo_v1.txt` |     0.815 |       83% | **4** checks        |
| v2      | `voicemail_agent_demo_v2.txt` |     0.905 |       89% | **3** checks        |

## Per-metric pass rate across versions

| Metric                     |   v0 |   v1 |   v2 |
| -------------------------- | ---: | ---: | ---: |
| Correct Screening          |  57% |  71% |  86% |
| Message Captured           |  71% |  71% |  71% |
| Persona Consistency        |  71% | 100% | 100% |
| Caller Sentiment / Respect | 100% | 100% | 100% |
| Task Completion            |  57% |  71% |  86% |

## Per-scenario detail

Each cell is the metric score with PASS/FAIL. Watch cells flip ❌→✅ left-to-right as the prompt improves.

### The Spammer — expects **block**

| Metric                     |       v0 |       v1 |       v2 |
| -------------------------- | -------: | -------: | -------: |
| Correct Screening          |  0.00 ❌ |  0.00 ❌ |  1.00 ✅ |
| Message Captured           |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Persona Consistency        |  0.90 ✅ |  1.00 ✅ |  1.00 ✅ |
| Caller Sentiment / Respect |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Task Completion            |  0.00 ❌ |  0.00 ❌ |  0.00 ❌ |
| _weighted_                 | **0.53** | **0.55** | **0.82** |

### The Important Client — expects **allow**

| Metric                     |       v0 |       v1 |       v2 |
| -------------------------- | -------: | -------: | -------: |
| Correct Screening          |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Message Captured           |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Persona Consistency        |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Caller Sentiment / Respect |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Task Completion            |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| _weighted_                 | **1.00** | **1.00** | **1.00** |

### The Friend — expects **take_message**

| Metric                     |       v0 |       v1 |       v2 |
| -------------------------- | -------: | -------: | -------: |
| Correct Screening          |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Message Captured           |  0.67 ❌ |  0.33 ❌ |  0.67 ❌ |
| Persona Consistency        |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Caller Sentiment / Respect |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Task Completion            |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| _weighted_                 | **0.93** | **0.85** | **0.93** |

### The Vague Caller — expects **take_message**

| Metric                     |       v0 |       v1 |       v2 |
| -------------------------- | -------: | -------: | -------: |
| Correct Screening          |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Message Captured           |  0.33 ❌ |  0.33 ❌ |  0.67 ❌ |
| Persona Consistency        |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Caller Sentiment / Respect |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Task Completion            |  1.00 ✅ |  0.80 ✅ |  1.00 ✅ |
| _weighted_                 | **0.85** | **0.82** | **0.93** |

### The Persistent Salesperson — expects **block**

| Metric                     |       v0 |       v1 |       v2 |
| -------------------------- | -------: | -------: | -------: |
| Correct Screening          |  0.00 ❌ |  1.00 ✅ |  0.00 ❌ |
| Message Captured           |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Persona Consistency        |  0.00 ❌ |  0.90 ✅ |  0.90 ✅ |
| Caller Sentiment / Respect |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Task Completion            |  0.00 ❌ |  0.00 ❌ |  1.00 ✅ |
| _weighted_                 | **0.36** | **0.80** | **0.71** |

### The Emergency Caller — expects **allow**

| Metric                     |       v0 |       v1 |       v2 |
| -------------------------- | -------: | -------: | -------: |
| Correct Screening          |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Message Captured           |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Persona Consistency        |  1.00 ✅ |  0.90 ✅ |  1.00 ✅ |
| Caller Sentiment / Respect |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Task Completion            |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| _weighted_                 | **1.00** | **0.98** | **1.00** |

### The Wrong Number — expects **block**

| Metric                     |       v0 |       v1 |       v2 |
| -------------------------- | -------: | -------: | -------: |
| Correct Screening          |  0.00 ❌ |  0.00 ❌ |  1.00 ✅ |
| Message Captured           |  1.00 ✅ |  1.00 ✅ |  1.00 ✅ |
| Persona Consistency        |  0.00 ❌ |  0.90 ✅ |  1.00 ✅ |
| Caller Sentiment / Respect |  1.00 ✅ |  1.00 ✅ |  0.90 ✅ |
| Task Completion            |  0.00 ❌ |  1.00 ✅ |  0.80 ✅ |
| _weighted_                 | **0.36** | **0.71** | **0.95** |

## Prompt versions

The exact system prompt evaluated at each step (the artifact that changes between versions).

### v0 — baseline (`voicemail_agent_v0.txt`)

```text
You are a voicemail assistant. The owner can't come to the phone.

Take a message from the caller and be friendly.
```

### v1 — auto-improved (`voicemail_agent_demo_v1.txt`)

```text
You are a voicemail assistant acting as the owner's personal assistant. If the caller is a known friend or leaves a clear message with their name, purpose, and callback information, politely take the message and confirm you will pass it on. If the caller is a spammer, persistent salesperson, wrong number, or otherwise irrelevant, do not take a message—politely end the call without inviting a message or promising a callback. Always be friendly but firm in screening calls to block unwanted contacts while capturing important messages completely.
```

### v2 — auto-improved (`voicemail_agent_demo_v2.txt`)

```text
You are a voicemail assistant acting as the owner's personal assistant. When a caller is a known friend or leaves a clear message with their name, purpose, and a callback number, politely take the message and confirm you will pass it on. If the caller is a spammer, persistent salesperson, wrong number, or otherwise irrelevant, do not take a message—politely end the call without inviting a message or promising a callback. If the caller leaves an incomplete message missing either their name or callback number, politely ask for the missing information before deciding to take the message. Always be friendly but firm in screening calls to block unwanted contacts while capturing important messages completely.
```
