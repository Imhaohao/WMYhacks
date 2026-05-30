# Gotchu — onboarding web UI

Owner-facing landing page and setup wizard for the voicemail agent.

## Run locally

Terminal 1 — onboarding API (port 8787):

```bash
cd server
uv run python onboarding_api.py
```

Terminal 2 — web app (port 5173):

```bash
cd web
npm install
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173)

Optional — voice bot for **Try call**:

```bash
cd server
ENV=local uv run bot-nemotron.py
```

Then open [http://localhost:7860](http://localhost:7860) from the Try page.

## Pages

| Route | Purpose |
|---|---|
| `/` | Hero + integrations orbit + CTA |
| `/setup` | 5-step wizard (profile, vCard, calendar, health, try) |
| `/try` | Interactive phone demo + WebRTC link |

## API

Proxied via Vite to `http://127.0.0.1:8787`:

- `GET/POST /api/owner-config` — saved to `server/owner_config.json`
- `POST /api/contacts/upload` — writes `server/contacts.vcf`
- `GET /api/health` — env + contacts preflight
