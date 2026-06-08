# Production Onboarding Boundary

The public Vercel site is deployed at
`https://web-omega-tawny-51.vercel.app`. For the current demo stack,
`VITE_ONBOARDING_API_URL` points at the public voice/API worker
(`https://grievous-itunes-lip.ngrok-free.dev`), so onboarding traffic uses
`/api/*` on the same HTTPS origin that Twilio uses for `POST /` and `/ws`.

## What Exists

`tenant_store.py` provides the tenant-isolated cloud boundary:

- DynamoDB-first agent records keyed by an unguessable tenant id.
- A one-time onboarding capability token; only its SHA-256 hash is persisted.
- Owner profile, assigned Twilio number, forwarding confirmation, consented
  Gradium clone metadata, and derived diction notes.
- No raw voice sample and no raw diction reply persistence.
- Runtime lookup by the Twilio number that received the call.
- Runtime lookup by `ForwardedFrom` for shared forwarded calls, so multiple
  tenants can forward missed calls to one Twilio number when the carrier
  provides the original owner number.

`onboarding_api.py` exposes guarded tenant routes:

- `POST /api/onboarding/sessions`
- `GET /api/onboarding/sessions/{tenant_id}`
- `POST /api/onboarding/sessions/{tenant_id}/profile`
- `POST /api/onboarding/sessions/{tenant_id}/voicemail-forwarding`
- `POST /api/onboarding/sessions/{tenant_id}/diction`
- `POST /api/onboarding/sessions/{tenant_id}/voice-clone`
- `DELETE /api/onboarding/sessions/{tenant_id}/voice-clone`
- `GET /api/onboarding/sessions/{tenant_id}/google/{gmail|calendar}/status`
- `GET /api/onboarding/sessions/{tenant_id}/google/{gmail|calendar}/auth-url`
- `GET /api/onboarding/google/{gmail|calendar}/callback`
- `POST /api/onboarding/sessions/{tenant_id}/google/{gmail|calendar}/disconnect`
- `POST /api/admin/onboarding/sessions/{tenant_id}/agent-phone`

The voice runtime selects the tenant by Twilio destination number for dedicated
tenant numbers, or by Twilio's `ForwardedFrom` field for shared forwarding. It
uses that tenant's owner SMS target, consented voice clone, derived diction
prompt, and persistence partition. If a tenant connects Google, runtime actions
use that tenant's Gmail/Calendar token. It never inherits the demo owner's
contacts, Gmail, Calendar, or outbox bridge.

## Current Live Evidence

Verified on 2026-06-07:

- The Vercel production alias `https://web-omega-tawny-51.vercel.app` serves the
  cloud onboarding UI.
- The public voice/API worker reports `{"status":"ready","transports":["twilio"]}`.
- `POST /` returns TwiML with stream parameters for `From`, `To`, and
  `ForwardedFrom`, avoiding a fragile Twilio REST lookup during call startup.
- `GET /api/onboarding/status` reports DynamoDB tenant storage with
  `TENANT_STORE_REQUIRE_CLOUD=true` and shared forwarding enabled for
  `+16506444731`.
- A fresh public onboarding smoke created a tenant session in DynamoDB, saved a
  derived diction profile, read it back through the public API, and did not
  expose `voice_clone_id` or the onboarding token.
- Real Gradium voice upload remains an acceptance gate that requires a
  consented owner audio sample. Unit coverage proves the API attaches a returned
  Gradium voice id, hides it from the public agent response, and best-effort
  deletes the clone if cloud attach fails or the owner resets their sample.
- The deployed voice step requires a measured browser recording duration and
  shows 20-60 second guidance. The live API rejects a 4-second tenant voice
  upload with HTTP 422 before any Gradium request.
- Tenant calls do not inherit the demo owner's global `ASSISTANT_MODE`. Private
  owner tools authorize only when the caller matches the tenant owner phone, and
  tenant-owner Gmail/Calendar reads use tenant Google tokens instead of the demo
  owner's token files.

## Required Before Public Activation

1. Deploy `onboarding_api.py` behind HTTPS with authentication and rate limits.
   For the current single-domain demo stack, set
   `ENABLE_ONBOARDING_API_ON_VOICE_WORKER=true` on `bot-nemotron.py` and point
   `VITE_ONBOARDING_API_URL` at the same public voice origin; Twilio keeps using
   `POST /` and `/ws`, while onboarding uses `/api/*`. If the frontend remains
   on Vercel, set `WEB_APP_URL=https://web-omega-tawny-51.vercel.app` on the
   voice/API worker so OAuth popups and CORS target the frontend origin.
2. Set `TENANT_STORE_REQUIRE_CLOUD=true`.
3. Set `ENABLE_PUBLIC_TENANT_ONBOARDING=true`.
4. Configure `VITE_ONBOARDING_API_URL` on the Vercel project.
5. Configure Twilio tenant-number provisioning:
   - safest demo path: assign each paid Twilio number through the admin
     phone-assignment endpoint. It wires the inbound voice webhook
     automatically.
   - self-serve from pre-purchased inventory: set
     `ENABLE_AUTO_AGENT_PHONE_ASSIGNMENT=true` and
     `TENANT_AGENT_PHONE_POOL=+1...,+1...`; the first free pool number is
     assigned during tenant activation.
   - self-serve shared forwarding: set
     `ENABLE_SHARED_FORWARDING_ROUTING=true` and
     `SHARED_FORWARDING_PHONE_NUMBER=<owned Twilio number>` (or rely on
     `TWILIO_PHONE_NUMBER`). Activation gives each tenant the same forwarding
     destination and the runtime routes by Twilio's `ForwardedFrom` field when
     the caller's carrier provides it. This avoids buying one number per tenant,
     but needs a real carrier-forwarded call acceptance test.
   - self-serve live purchase: set `ENABLE_AUTO_AGENT_PHONE_PURCHASE=true`
     only after authentication, rate limits, spend controls, and abuse
     monitoring are in place. When the pool is unavailable or exhausted, tenant
     activation searches Twilio Local inventory, skips address-required numbers
     by default, purchases one voice-capable number, points its Voice webhook at
     `PUBLIC_BASE_URL`, and stores it on the tenant record. Set
     `TWILIO_AUTO_PURCHASE_DAILY_LIMIT` to the maximum purchased numbers per
     rolling day; purchase mode requires DynamoDB tenant storage by default via
     `TWILIO_AUTO_PURCHASE_REQUIRE_CLOUD=true`.
6. Set `TENANT_RUNTIME_REQUIRE_MATCH=true` on production voice workers.
7. Configure tenant-scoped Google OAuth token storage before enabling Calendar
   or Gmail OAuth for arbitrary users:
   `TENANT_GOOGLE_TOKEN_STORE=secretsmanager`,
   `TENANT_GOOGLE_SECRET_PREFIX=gotchu/tenant-google`.
8. Run a real phone-call acceptance test for each newly provisioned number or
   shared-forwarding owner phone.
9. Run a real Gradium upload acceptance test with a consented audio sample, then
   exercise `DELETE /api/onboarding/sessions/{tenant_id}/voice-clone` before
   discarding the tenant.

The backend must set `PUBLIC_BASE_URL` to the HTTPS voice-bot origin before an
admin assigns a number. `TWILIO_VOICE_PATH` defaults to `/`.

For tenant Google OAuth, add these callback URIs to the Google Web Application
OAuth client in addition to the single-owner demo callbacks:

- `<PUBLIC_BASE_URL>/api/onboarding/google/gmail/callback`
- `<PUBLIC_BASE_URL>/api/onboarding/google/calendar/callback`

`ENABLE_PUBLIC_VOICE_CLONING=true` is deliberately separate because it enables
a paid Gradium operation. Keep authentication, abuse controls, and the deletion
workflow enabled together. The tenant delete/reset route clears local metadata
and calls Gradium's `DELETE /voices/{voice_uid}` API best-effort.
