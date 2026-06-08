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

export type OnboardingStatus = {
  private_activation_enabled: boolean
  tenant_onboarding_enabled?: boolean
  tenant_storage?: {
    backend: string
    require_cloud: boolean
  }
  tenant_phone_provisioning?: {
    pool_enabled: boolean
    pool_size: number
    purchase_enabled: boolean
    purchase_daily_limit?: number
    purchases_today?: number
    purchase_requires_cloud?: boolean
    shared_forwarding_enabled?: boolean
    shared_forwarding_phone?: string
  }
  agent_phone: string
  voicemail_forwarding_confirmed: boolean
  voice_clone_ready: boolean
  voice_clone_name: string
  diction_examples: string
  diction_profile: DictionProfile
}

export type DictionProfile = {
  ready: boolean
  sample_count?: number
  phrases: string[]
  style_notes: string[]
  lines: string[]
}

export type TenantAgent = {
  tenant_id: string
  activation_status: "draft" | "provisioning" | "ready"
  display_name: string
  owner_phone: string
  owner_email: string
  timezone: string
  agent_phone: string
  agent_phone_source: string
  agent_phone_assigned_at: string
  forwarding_phone: string
  forwarding_phone_assigned_at: string
  routing_mode: string
  voicemail_forwarding_confirmed: boolean
  voice_clone_name: string
  voice_clone_status: "not_started" | "ready"
  voice_clone_updated_at: string
  gmail_connected_email: string
  gmail_connected_at: string
  calendar_connected_email: string
  calendar_connected_at: string
  diction_profile_phrases: string[]
  diction_profile_style_notes: string[]
  diction_profile_lines: string[]
  diction_profile_updated_at: string
}

export type TenantProfileInput = {
  display_name: string
  owner_phone: string
  owner_email: string
  timezone: string
}

export type TenantGoogleService = "gmail" | "calendar"

export type TenantGoogleStatus = {
  configured: boolean
  connected: boolean
  email: string
  storage: string
}

export type TenantPhoneAssignment = {
  mode: "deferred" | "disabled" | "pool" | "purchase" | "existing" | "shared_forwarding"
  assigned: boolean
  agent_phone?: string
  forwarding_phone?: string
  routing_mode?: string
  detail?: string
}

const ONBOARDING_API_ORIGIN = (import.meta.env.VITE_ONBOARDING_API_URL ?? "").replace(/\/$/, "")
const API = `${ONBOARDING_API_ORIGIN}/api`

export function tenantOnboardingConfigured(): boolean {
  return Boolean(ONBOARDING_API_ORIGIN)
}

function tenantHeaders(token: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-Onboarding-Token": token,
  }
}

export async function createTenantSession(
  profile: TenantProfileInput,
): Promise<{
  ok: boolean
  onboarding_token: string
  storage: string
  agent: TenantAgent
  phone_assignment: TenantPhoneAssignment
}> {
  const res = await fetch(`${API}/onboarding/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  })
  if (!res.ok) throw new Error("Tenant onboarding session creation failed")
  return res.json()
}

export async function activateTenantAgent(
  tenantId: string,
  token: string,
): Promise<{ ok: boolean; agent: TenantAgent; phone_assignment: TenantPhoneAssignment }> {
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}/activate`, {
    method: "POST",
    headers: { "X-Onboarding-Token": token },
  })
  if (!res.ok) throw new Error("Tenant activation failed")
  return res.json()
}

export async function fetchTenantSession(tenantId: string, token: string): Promise<{ ok: boolean; agent: TenantAgent }> {
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}`, {
    headers: { "X-Onboarding-Token": token },
  })
  if (!res.ok) throw new Error("Tenant onboarding session load failed")
  return res.json()
}

export async function saveTenantProfile(
  tenantId: string,
  token: string,
  profile: TenantProfileInput,
): Promise<{ ok: boolean; agent: TenantAgent }> {
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}/profile`, {
    method: "POST",
    headers: tenantHeaders(token),
    body: JSON.stringify(profile),
  })
  if (!res.ok) throw new Error("Tenant profile save failed")
  return res.json()
}

export async function confirmTenantVoicemailForwarding(
  tenantId: string,
  token: string,
  confirmed: boolean,
): Promise<{ ok: boolean; agent: TenantAgent }> {
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}/voicemail-forwarding`, {
    method: "POST",
    headers: tenantHeaders(token),
    body: JSON.stringify({ confirmed }),
  })
  if (!res.ok) throw new Error("Tenant forwarding confirmation failed")
  return res.json()
}

export async function createTenantVoiceClone(
  tenantId: string,
  token: string,
  file: File,
  voiceName: string,
  durationSeconds: number,
): Promise<{ ok: boolean; updated_at: string; agent: TenantAgent }> {
  const form = new FormData()
  form.append("file", file)
  form.append("consent", "true")
  form.append("voice_name", voiceName)
  form.append("duration_seconds", String(durationSeconds))
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}/voice-clone`, {
    method: "POST",
    headers: { "X-Onboarding-Token": token },
    body: form,
  })
  if (!res.ok) throw new Error("Tenant voice clone creation failed")
  return res.json()
}

export async function deleteTenantVoiceClone(
  tenantId: string,
  token: string,
): Promise<{ ok: boolean; updated_at: string; voice_deleted: { deleted: boolean; skipped?: boolean; detail?: string }; agent: TenantAgent }> {
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}/voice-clone`, {
    method: "DELETE",
    headers: { "X-Onboarding-Token": token },
  })
  if (!res.ok) throw new Error("Tenant voice clone reset failed")
  return res.json()
}

export async function saveTenantDictionProfile(
  tenantId: string,
  token: string,
  samples: string[],
  phrases: string[],
): Promise<{ ok: boolean; updated_at: string; profile: DictionProfile; agent: TenantAgent }> {
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}/diction`, {
    method: "POST",
    headers: tenantHeaders(token),
    body: JSON.stringify({ samples, phrases }),
  })
  if (!res.ok) throw new Error("Tenant diction save failed")
  return res.json()
}

export async function fetchTenantGoogleStatus(
  tenantId: string,
  token: string,
  service: TenantGoogleService,
): Promise<{ ok: boolean; service: TenantGoogleService; status: TenantGoogleStatus; agent: TenantAgent }> {
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}/google/${service}/status`, {
    headers: { "X-Onboarding-Token": token },
  })
  if (!res.ok) throw new Error("Tenant Google status failed")
  return res.json()
}

export async function getTenantGoogleAuthUrl(
  tenantId: string,
  token: string,
  service: TenantGoogleService,
): Promise<{ ok: boolean; service: TenantGoogleService; url: string }> {
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}/google/${service}/auth-url`, {
    headers: { "X-Onboarding-Token": token },
  })
  if (!res.ok) throw new Error("Could not start tenant Google OAuth")
  return res.json()
}

export async function disconnectTenantGoogle(
  tenantId: string,
  token: string,
  service: TenantGoogleService,
): Promise<{ ok: boolean; service: TenantGoogleService; disconnected: boolean; agent: TenantAgent }> {
  const res = await fetch(`${API}/onboarding/sessions/${tenantId}/google/${service}/disconnect`, {
    method: "POST",
    headers: { "X-Onboarding-Token": token },
  })
  if (!res.ok) throw new Error("Tenant Google disconnect failed")
  return res.json()
}

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

export async function fetchOnboardingStatus(): Promise<OnboardingStatus> {
  const res = await fetch(`${API}/onboarding/status`)
  if (!res.ok) throw new Error("Onboarding status failed")
  return res.json()
}

export async function confirmVoicemailForwarding(confirmed: boolean): Promise<{ ok: boolean; confirmed: boolean }> {
  const res = await fetch(`${API}/onboarding/voicemail-forwarding`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmed }),
  })
  if (!res.ok) throw new Error("Voicemail forwarding confirmation failed")
  return res.json()
}

export async function createVoiceClone(
  file: File,
  voiceName: string,
  durationSeconds: number,
): Promise<{ ok: boolean; voice_clone_ready: boolean; voice_clone_name: string; updated_at: string }> {
  const form = new FormData()
  form.append("file", file)
  form.append("consent", "true")
  form.append("voice_name", voiceName)
  form.append("duration_seconds", String(durationSeconds))
  const res = await fetch(`${API}/onboarding/voice-clone`, { method: "POST", body: form })
  if (!res.ok) throw new Error("Voice clone creation failed")
  return res.json()
}

export async function previewDictionProfile(
  samples: string[],
  phrases: string[],
): Promise<DictionProfile> {
  const res = await fetch(`${API}/onboarding/diction/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ samples, phrases }),
  })
  if (!res.ok) throw new Error("Diction preview failed")
  return res.json()
}

export async function saveDictionProfile(
  samples: string[],
  phrases: string[],
): Promise<{ ok: boolean; updated_at: string; profile: DictionProfile }> {
  const res = await fetch(`${API}/onboarding/diction`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ samples, phrases }),
  })
  if (!res.ok) throw new Error("Diction save failed")
  return res.json()
}

export function botWebRtcUrl(): string {
  return import.meta.env.VITE_BOT_WEBRTC_URL ?? "http://localhost:7860"
}
