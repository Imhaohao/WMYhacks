# Gotchu

Can't pick up? We gotchu.

## What is this?

When your phone goes to voicemail, Gotchu answers for you. Gotchu references your contact information, chat history, and calendar to respond in exactly the way you would.

## Demo

[Demo Link](https://youtu.be/7em2jd1y9fo)

## Tech Stack

**Cekura**: We used Cekura extensively during our development process, crafting our system prompt with its evaluation and improvement pipeline. We ran rounds of experiments of seven different scenarios (including but not limited to a spammer, a client, and a friend), evaluated using Cekura on metrics such as persona consistency and task completion. **Cekura** allowed us to build our system prompt _iteratively_, adapting to failures and successes in actual simulated calls.

We wanted Cekura to ensure that our system prompt supported the most effective user-agent interactions possible by optimizing five specific metrics (Correct Screening, Message Captured, Persona Consistency, Sentiment/Respect, and Task Completion). Cekura helped refine our prompt to improve our pass rate by over 18%, converting failed cases to passed cases each time.

**At a glance** (alternatively, [view our experiment in more detail](https://github.com/Imhaohao/WMYhacks/blob/main/server/eval/demo_report_openai_2rounds.md)):
| Version | Pass rate | number FAIL→PASS |
| ------- | --------: | ---------------- |
| v0 | 71% | — (baseline) |
| v1 | 83% | **4** checks |
| v2 | 89% | **3** checks |

**Nemotron**: Nemotron gave our voice agent the distinct persona of its user. It interpreted the user's talking style from the user's past iMessages and Claude prompt history to build an accurate, authentic persona. Nemotron was the backbone of this voice agent, responsible for maintaining the persona, interpreting tone and urgency, and formulating responses to answer appropriately.

**Pipecat**: The framework we used to orchestrate our voice agent. Pipecat connects to **Twilio**, which allowed us to talk to our voice agent through a phone call's voicemail.

## What We Did Today

This entire project was built from scratch today.

# Feedback

**Cekura**: Our main issue with the Cekura platform was its documentation: while including it in our development process went well and was fairly straightforward, we struggled to include it in our real-world production calls, finding it difficult to send call logs to the dashboard. It would be useful to have an example repository or file with a demonstration use of sending call logs to the dashboard in production.

**NVIDIA**: The model's responses felt rigid at times, taking much more experimentation than expected to refine responses to feel more natural and accurate to the persona. Additionally, latency was an issue, causing long waiting times before receiving responses.

**Pipecat**: It was difficult to identify when there was a mismatch in the audio in sample rate between the model and the user input. This made it very difficult to debug as well, because the user could hear the agent initially talking when they called into the twilio number, but then the agent did not do anything, and there were no helpful error messages either. However, the pipecat engineers were very helpful in identifying the issue, though this still took up a lot of time.
