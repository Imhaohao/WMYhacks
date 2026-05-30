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

export type ContactsUploadResult = {
  ok: boolean
  count: number
  summary: string
  sample_names: string[]
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

export function botWebRtcUrl(): string {
  return import.meta.env.VITE_BOT_WEBRTC_URL ?? "http://localhost:7860"
}
