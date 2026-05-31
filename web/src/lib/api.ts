export type OwnerConfig = {
  display_name: string
  owner_phone: string
  owner_email: string
  timezone: string
  manual_availability: string
  setup_complete: boolean
  message_context_summary: string
  message_context_source: "manual" | "imessage"
  message_context_synced_at: string
}

export type MessageContext = {
  message_context_summary: string
  message_context_source: "manual" | "imessage"
  message_context_synced_at: string
}

export type HealthCheck = {
  ok: boolean
  checks: Record<string, { ok: boolean; detail: string }>
  contacts: { count: number; summary: string; sample_names: string[] }
}

export type ServiceStatus = {
  gmail: {
    ok: boolean
    mode: "google_oauth" | "smtp" | "mcp_draft_bridge"
    detail: string
    connected?: boolean
    configured?: boolean
    target: string
    pending_actions: number
  }
  calendar: {
    ok: boolean
    mode: "google_oauth"
    detail: string
    connected?: boolean
    configured?: boolean
    target: string
    timezone: string
    pending_actions: number
  }
  messages: {
    ok: boolean
    mode: "redacted_digest"
    detail: string
    source: "manual" | "imessage"
    synced_at: string
    preview: string
  }
  bridge: {
    ok: boolean
    detail: string
    pending_actions: number
  }
}

export type BridgeSpec = {
  action_id?: string
  connector: string
  args?: Record<string, unknown>
  note?: string
  needs?: string | null
  raw?: Record<string, unknown>
}

export type BridgeSpecsResult = {
  kind: string
  count: number
  specs: BridgeSpec[]
}

export type ContactsUploadResult = {
  ok: boolean
  count: number
  summary: string
  sample_names: string[]
}

export type CalendarStatus = {
  connected: boolean
  configured: boolean
  email: string | null
  detail: string
}

export type GmailStatus = {
  connected: boolean
  configured: boolean
  email: string | null
  detail: string
}

const API = "/api"

export async function fetchOwnerConfig(): Promise<OwnerConfig> {
  const res = await fetch(`${API}/owner-config`)
  if (!res.ok) throw new Error("Failed to load config")
  return res.json()
}

export async function saveOwnerConfig(config: Partial<OwnerConfig>): Promise<OwnerConfig> {
  const res = await fetch(`${API}/owner-config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  })
  if (!res.ok) throw new Error("Failed to save config")
  return res.json()
}

export async function fetchHealth(): Promise<HealthCheck> {
  const res = await fetch(`${API}/health`)
  if (!res.ok) throw new Error("Health check failed")
  return res.json()
}

export async function fetchServices(): Promise<ServiceStatus> {
  const res = await fetch(`${API}/services`)
  if (!res.ok) throw new Error("Service status failed")
  return res.json()
}

export async function fetchBridgeSpecs(kind: "gmail" | "calendar" | "all"): Promise<BridgeSpecsResult> {
  const res = await fetch(`${API}/bridge/specs?kind=${encodeURIComponent(kind)}`)
  if (!res.ok) throw new Error("Bridge specs failed")
  return res.json()
}

export async function uploadContacts(file: File): Promise<ContactsUploadResult> {
  const form = new FormData()
  form.append("file", file)
  const res = await fetch(`${API}/contacts/upload`, { method: "POST", body: form })
  if (!res.ok) throw new Error("Upload failed")
  return res.json()
}

export async function syncMessageContext(): Promise<MessageContext> {
  const res = await fetch(`${API}/context/sync-messages`, { method: "POST" })
  if (!res.ok) throw new Error("Sync failed")
  return res.json()
}

export async function fetchCalendarStatus(): Promise<CalendarStatus> {
  const res = await fetch(`${API}/calendar/google/status`)
  if (!res.ok) throw new Error("Calendar status failed")
  return res.json()
}

export async function getCalendarAuthUrl(): Promise<{ ok: boolean; url: string | null; detail?: string }> {
  const res = await fetch(`${API}/calendar/google/auth-url`)
  if (!res.ok) throw new Error("Could not start Google OAuth")
  return res.json()
}

export async function disconnectCalendar(): Promise<void> {
  const res = await fetch(`${API}/calendar/google/disconnect`, { method: "POST" })
  if (!res.ok) throw new Error("Disconnect failed")
}

export async function fetchGmailStatus(): Promise<GmailStatus> {
  const res = await fetch(`${API}/gmail/google/status`)
  if (!res.ok) throw new Error("Gmail status failed")
  return res.json()
}

export async function getGmailAuthUrl(): Promise<{ ok: boolean; url: string | null; detail?: string }> {
  const res = await fetch(`${API}/gmail/google/auth-url`)
  if (!res.ok) throw new Error("Could not start Gmail OAuth")
  return res.json()
}

export async function disconnectGmail(): Promise<void> {
  const res = await fetch(`${API}/gmail/google/disconnect`, { method: "POST" })
  if (!res.ok) throw new Error("Disconnect failed")
}

export function botWebRtcUrl(): string {
  return import.meta.env.VITE_BOT_WEBRTC_URL ?? "http://localhost:7860"
}
