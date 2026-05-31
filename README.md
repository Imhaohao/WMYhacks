# YC Voice Agents Hackathon

Welcome to the YC Voice Agents Hackathon, hosted by [Cekura](https://cekura.com) and [Daily](https://daily.co), in partnership with [NVIDIA](https://nvidia.com), [AWS](https://aws.amazon.com), and [Twilio](https://twilio.com).

The goal of this event is to learn about building, scaling, evaluating, and continuously improving voice agents.

## Schedule, rules, and prizes

This is a one-day event. Please arrive by 8:30. We'll kick things off at 9:00.

### Schedule

  - 8:00 AM – Doors open & registration
  - 8:30 AM – Breakfast
  - 9:00 AM – Welcome / Hackathon begins
  - 12:00 PM – Lunch
  - 6:00 PM – Submissions due
  - 6:00 - 8:00 PM – Dinner, demos, and conversation
  - 8:00 PM – Judges' presentations
  - 9:00 PM – We all go home

### General guidance

First of all, please respect the YC space. We very much appreciate YC hosting these events. Stay in the designated areas, clean up after meals, and in general be a good guest.

Build something new for this hackathon. Use the tools from Cekura to evaluate and improve the performance of what you build. Use Pipecat as the orchestration framework for your voice agent. We also encourage you to use the open source models from NVIDIA, but it's okay to use any models that work well for your project.

There will be engineers from Cekura, Daily, NVIDIA, AWS, and Twilio available to help you with your project. Don't hesitate to find us.

Judging will start at 6:00. In general, the judges want to showcase interesting projects rather than just pick winners. So don't worry too much about what the judges are looking for in a project. Build something that demonstrates creativity, is interesting on a technical level, or solves a real problem! But do keep in mind that the judges want to see great examples of using Cekura to improve voice agent performance, and using open source models from NVIDIA.


# Tech stack and starting points

This repo contains two Pipecat voice-agent entry points:

| Bot | Use case |
|---|---|
| **`bot-nemotron.py`** | **Primary hackathon build** — personal voicemail agent (Gotchu) on Nemotron + Gradium |
| **`bot-gpt.py`** | Original starter — **Field & Flower** flower-shop demo on GPT-4.1 + Gradium |

The Nemotron bot answers calls on the owner's behalf, captures a complete message, adapts tone to the caller, and routes notifications (SMS, email, calendar) while persisting each call to AWS or local fallback storage.

The GPT bot is unchanged from the Pipecat starter: callers order a bouquet from a mocked catalog (`mock_backend.py`).

## Architecture

### Voice pipeline (`bot-nemotron.py`)

Each call runs one Pipecat pipeline:

```
Browser (WebRTC) or Twilio phone
        │
        ▼
  Transport (SmallWebRTC / Twilio WebSocket)
        │
        ▼
  NVIDIA Nemotron STT  ──►  P3 tone listener  ──►  LLM context + VAD
        │                         │
        │                         └── infers caller tone from final transcripts
        ▼
  Nemotron 3 Super 120B (vLLM on AWS)  +  function tools
        │
        ▼
  Gradium TTS (cloned owner voice)
        │
        ▼
  Audio out + transcript
```

On disconnect, **P3** persists the voicemail record (snapshot + actions taken) via `persona_tools.on_call_finished()`.

### Per-call state (`interfaces.py`)

All teammates share one `CallState` dict per call:

| Key | Owner | Purpose |
|---|---|---|
| `voicemail` | **P1** | Transcript, summary, caller number, action items, SMS/email flags |
| `caller_snapshot` | **P3** | Live tone, urgency, relationship, persona match |
| `persona_context` | **P2** | Owner availability, priority contacts, message digest — injected into the system prompt |

Tools register through a single **`TOOL_REGISTRY`** list. Each module appends its handlers at import time; `bot-nemotron.py` binds `call_state` into tools that need it.

**LLM tools (current):**

| Tool | Owner | When the agent uses it |
|---|---|---|
| `record_message` | P1 | Caller name, callback number, subject, and urgency are confirmed |
| `notify_owner_sms` | P1 | Urgent voicemail — Twilio SMS to owner |
| `end_call` | P1 | After goodbye — hangs up |
| `update_caller_snapshot` | P3 | Learns caller details or re-infers tone from wording |
| `lookup_persona` | P3 | Matches Twilio caller ID to contacts; exact-name allowlist fallback for WebRTC demos |
| `get_calendar_context_for_caller` | P3 | After contact approval only, derives one caller-facing reason from Calendar without exposing the schedule |
| `send_owner_email` | P3 | Sends owner a summary email (SMTP or outbox fallback) |
| `book_callback_slot` | P3 | Tentative calendar callback (Calendar MCP or outbox fallback) |

Calendar read is wired through `google_calendar.py`: free/busy checks support
callbacks, while contact-gated context answers return at most one safe reason.

### Persistence — the agent's memory (`persistence.py`)

Finished voicemails and Cekura eval runs are written through one API. Backend resolution (first match wins):

1. **DynamoDB** — table `ff-voicemails` (override with `PERSIST_DDB_TABLE`)
2. **S3** — bucket from `PERSIST_S3_BUCKET`
3. **Local file** — `server/aws_store/records.jsonl` (demo default when AWS is unavailable)

Requires AWS credentials in env for the cloud path. No raw audio is stored — structured fields only.

The cleaned derived owner persona is also stored as one overwriteable cloud
record in the same DynamoDB table, with optional S3 fallback. Run
`uv run python -m ingest.refresh` to republish it after local context changes.
At startup the voice agent reads the cloud persona first, so Pipecat Cloud and
other machines share the same context without copying a generated local file.

Queued email/calendar actions land in `server/outbox/actions.jsonl` until `action_bridge.py` or live connectors fulfill them.

### Owner onboarding

| Component | Path | Role |
|---|---|---|
| Web UI | `web/` | Landing, setup wizard, try-call page (Vite + React) |
| Onboarding API | `server/onboarding_api.py` | FastAPI — saves `owner_config.json`, vCard upload, message digest |
| Owner config | `server/owner_config.py` | Builds `persona_context` for the system prompt at call start |
| Contacts | `server/contacts.py` + `contacts.vcf` | Known-caller lookup by phone number; exact-name WebRTC demo allowlist |

Run the onboarding stack:

```bash
# Terminal 1 — API on :8787
cd server && uv run python onboarding_api.py

# Terminal 2 — UI on :5173
cd web && npm install && npm run dev
```

See [`web/README.md`](web/README.md) for routes and details.

### Environment and secrets

The Nemotron bot loads env from **`server/.env`** then **`server/.env.local`** (local overrides). Copy `server/.env.example` to `.env` and put machine-specific keys in `.env.local` — neither file should be committed.

Integration docs for teammates: [`server/INTEGRATION_P3.md`](server/INTEGRATION_P3.md), [`server/P3_NEXT.md`](server/P3_NEXT.md).

## Version 1 — GPT-4.1 (Field & Flower starter)

You can start with this before the hackathon, if you want to. Or test GPT-4.1 and Nemotron side-by-side during the hackathon, using Cekura.

This bot only requires a Gradium API key and an OpenAI API key. Sign up for free at [Gradium](https://gradium.ai). We'll provide a code for Gradium credits, during the event.

- **STT:** [Gradium](https://gradium.ai)
- **LLM:** [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses) (GPT-4.1)
- **TTS:** [Gradium](https://gradium.ai)
- **Transports:** SmallWebRTC (local dev) and [Twilio](https://www.twilio.com/en-us) (production telephony)
- **Deploy target:** [Pipecat Cloud](https://pipecat.daily.co)

## Version 2 — Nemotron (Gotchu voicemail agent)

NVIDIA models hosted on AWS, available during the hackathon. We'll share endpoints for the NVIDIA ASR (STT) and LLM models at the beginning of the day.

- **STT:** [Nemotron Speech Streaming](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b)
- **LLM:** [Nemotron 3 Super 120B](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16)
- **TTS:** [Gradium](https://gradium.ai)
- **Transports:** SmallWebRTC (local dev) and Twilio (production telephony)
- **Deploy target:** [Pipecat Cloud](https://pipecat.daily.co)

## Develop locally

Get the bot running over WebRTC in your browser before you push to the cloud or wire up the phone, for a faster iteration loop.

### Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) package manager
- API keys for [OpenAI](https://platform.openai.com) and [Gradium](https://gradium.ai)

### Setup

1. **Clone and enter the server directory:**

   ```bash
   git clone https://github.com/pipecat-ai/yc-voice-agents-hackathon.git
   cd yc-voice-agents-hackathon/server
   ```

2. **Configure API keys:**

   ```bash
   cp .env.example .env
   # Fill in GRADIUM_API_KEY (required) and Nemotron URLs for bot-nemotron.py.
   # Optional: copy secrets to .env.local (loaded second, gitignored).
   # TWILIO_* keys are only needed when you wire up the phone.
   ```

3. **Install dependencies:**

   ```bash
   uv sync
   ```

4. **Run the bot:**

   ```bash
   ENV=local uv run bot-nemotron.py   # primary — voicemail agent
   # uv run bot-gpt.py                # starter — flower shop demo
   ```

   Open [http://localhost:7860](http://localhost:7860) and click **Connect** to start talking. First launch takes ~20s while Pipecat downloads VAD and turn-detection models.

## Deploy to Pipecat Cloud

Once the bot works locally, deploy to Pipecat Cloud and connect it to a Twilio phone number so anyone can call in.

### Prerequisites

1. [Sign up for Pipecat Cloud](https://pipecat.daily.co/sign-up)
2. Install the [Pipecat CLI](https://github.com/pipecat-ai/pipecat-cli) and log in:

   ```bash
   uv tool install pipecat-ai-cli
   pc cloud auth login
   ```

### Configure Twilio

1. [Add credits / upgrade your Twilio account](https://twil.io/yc-hack)

2. [Buy a phone number](https://help.twilio.com/articles/223135247) with voice capability.

3. Get your Pipecat Cloud organization name:

   ```bash
   pc cloud organizations list
   ```

4. [Create a TwiML Bin](https://www.twilio.com/docs/serverless/twiml-bins/getting-started#create-a-new-twiml-bin) with this configuration:

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <Response>
     <Connect>
       <Stream url="wss://api.pipecat.daily.co/ws/twilio">
         <Parameter name="_pipecatCloudServiceHost"
           value="flower-bot.YOUR_ORG_NAME"/>
       </Stream>
     </Connect>
   </Response>
   ```

   Replace `YOUR_ORG_NAME` with the org name from step 2.

5. [Attach the TwiML Bin](https://www.twilio.com/docs/serverless/twiml-bins/getting-started#wire-your-twiml-bin-up-to-an-incoming-phone-call) to your Twilio number: Go to [your phone numbers](https://console.twilio.com/go?to=/account/__account__/us1/senders-hub/list/phone-numbers/inventory) → select your
number → under **Voice Configuration**, set method to the **TwiML Bin** you created → Save.

6. [Optional] Use [Twilio Dev phone](https://www.twilio.com/docs/labs/dev-phone) for testing.

### Review the deployment configuration

Your deployment details are specified in the `pcc-deploy.toml` file. You can learn more about options in the [docs](https://docs.pipecat.ai/api-reference/cli/cloud/deploy#configuration-file-pcc-deploy-toml).

### Upload secrets

```bash
pc cloud secrets set flower-bot-secrets --file .env
```

This uploads everything from `.env` to Pipecat Cloud's secure storage. The bot reads from there at runtime, so you don't bake keys into the image.

### Deploy

Build and run your bot on Pipecat Cloud:

```bash
pc cloud deploy
```

Learn more about [cloud builds](https://docs.pipecat.ai/pipecat-cloud/guides/cloud-builds).

### Call your bot

Dial the Twilio number you set up. 🌷

## Test your agent with Cekura

[Cekura](https://cekura.com) tests and observes voice agents. For this hackathon, use it to **test the Pipecat bot you build in this repo** — run real conversations against it, score the transcripts, and fix what's failing before you demo.

### Sign up

Create your account at **[dashboard.cekura.ai](https://dashboard.cekura.ai)**. If you're approved for this hackathon, just sign up and your credits will show up automatically. If you don't see them, find someone from the Cekura team, they're on-site.

### Onboarding (or skip it)

On first login you'll land on a short setup flow that helps you create your first agent and test. Feel free to click through it — **or hit _Skip_** and jump straight to the dashboard if you'd rather set things up yourself. Either way takes a minute.

### Recommended: start by testing your agent (via Claude Code)

The fastest path — and what we recommend for the hackathon — is to drive Cekura from **Claude Code** using our MCP server + skills. You stay in your terminal, and Cekura handles agent creation, scenario generation, and running the test.

**1. Install the Cekura skills + MCP** (Claude Code marketplace plugin — bundles the skills, slash commands, and auto-configured MCP server):

```bash
/plugin marketplace add cekura-ai/cekura-skills
/plugin install cekura@cekura-skills
```

Repo: [github.com/cekura-ai/cekura-skills](https://github.com/cekura-ai/cekura-skills) · Full setup + other agents (Cursor, Codex, etc.): **[docs.cekura.ai → Claude Code guide](https://docs.cekura.ai/mcp/claude-code-guide)** and **[Skills](https://docs.cekura.ai/mcp/skills)**.

**2. Run an end-to-end test** of your agent with a single command:

```
/cekura-report
```

This spins up anything from 10–20 evaluators (what Cekura calls test cases), runs scenarios against your Pipecat agent, and gives you back a full report — transcripts, scores, and what failed — so you can iterate fast.

> When connecting your agent, **select `Pipecat` as the provider.** Details: [docs.cekura.ai → Pipecat](https://docs.cekura.ai/documentation/integrations/pipecat/automated).

## Learn more

### Pipecat

- [Pipecat Documentation](https://docs.pipecat.ai/)
- [Pipecat Cloud Deployment](https://docs.pipecat.ai/pipecat-cloud/introduction)
- [Pipecat Examples](https://github.com/pipecat-ai/pipecat-examples)
- [Pipecat Discord](https://discord.gg/pipecat)

### Twilio

- [Twilio Developer Hub](https://www.twilio.com/en-us/developers)
- [Twilio Documentation](https://www.twilio.com/docs)
- [Twilio Dev phone](https://www.twilio.com/docs/labs/dev-phone)

### Cekura

- [Claude Code guide](https://docs.cekura.ai/mcp/claude-code-guide) — MCP + skills setup
- [Cekura skills](https://docs.cekura.ai/mcp/skills) — all slash commands
- [Pipecat integration](https://docs.cekura.ai/documentation/integrations/pipecat/automated)
- [Cekura docs](https://docs.cekura.ai) · [dashboard](https://dashboard.cekura.ai)
