# Gotchu web app

Landing page, signup and the screened-calls inbox.

| Route | What it does |
|---|---|
| `/` | Landing page with an example screened call |
| `/onboarding` | Name, mobile and email, then the carrier code that forwards missed calls to the Gotchu line |
| `/calls` | The owner's screened calls, refreshed every 5 seconds |

The browser keeps the tenant id and onboarding token in `localStorage` and sends the token as `X-Onboarding-Token`.

## Run locally

```bash
cd server && uv run python onboarding_api.py   # API on :8787
cd web && npm install && npm run dev           # UI on :5173, proxies /api to :8787
```

Set `ONBOARDING_API_PROXY` to point the dev proxy at another API port. The API needs `ENABLE_PUBLIC_TENANT_ONBOARDING=true` and a phone mode such as `ENABLE_SHARED_FORWARDING_ROUTING=true`; see `server/PRODUCTION_ONBOARDING.md`.

## Deploy

On Vercel, `api/proxy.js` forwards `/api/*` to `ONBOARDING_API_TARGET_ORIGIN` (the voice worker's public origin), so preview URLs need no CORS entry.
