export type Agent = {
  tenant_id: string
  activation_status: "draft" | "ready"
  display_name: string
  owner_phone: string
  owner_email: string
  agent_phone: string
  forwarding_phone: string
  voicemail_forwarding_confirmed: boolean
}

export type ScreenedCall = {
  id: string
  received_at: string
  duration_seconds: number
  caller_name: string
  caller_number: string
  reason: string
  urgency: "normal" | "urgent" | string
  wants_callback: boolean
  callback_number: string
  callback_time: string
  caller_words: string
  owner_text: string
  texted: boolean
}

export type Profile = {
  display_name: string
  owner_phone: string
  owner_email: string
  timezone: string
}

export type Session = { tenantId: string; token: string }

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const API = `${(import.meta.env.VITE_ONBOARDING_API_URL ?? "").replace(/\/$/, "")}/api`

function detailMessage(detail: unknown): string {
  if (typeof detail === "string") return detail
  if (detail && typeof detail === "object" && "message" in detail) {
    return String((detail as { message: unknown }).message)
  }
  return ""
}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set("Content-Type", "application/json")
  if (token) headers.set("X-Onboarding-Token", token)
  const res = await fetch(`${API}${path}`, { ...init, headers })
  if (res.ok) return res.json() as Promise<T>
  const body = await res.json().catch(() => ({}))
  throw new ApiError(res.status, detailMessage(body.detail))
}

export function agentLine(agent: Agent): string {
  return agent.agent_phone || agent.forwarding_phone
}

export async function signUp(profile: Profile): Promise<{ session: Session; agent: Agent }> {
  const created = await request<{ onboarding_token: string; agent: Agent }>("/onboarding/sessions", {
    method: "POST",
    body: JSON.stringify(profile),
  })
  const session = { tenantId: created.agent.tenant_id, token: created.onboarding_token }
  const activated = await request<{ agent: Agent; phone_assignment: { assigned: boolean; detail?: string } }>(
    `/onboarding/sessions/${session.tenantId}/activate`,
    { method: "POST" },
    session.token,
  )
  if (!activated.phone_assignment.assigned) {
    throw new ApiError(409, activated.phone_assignment.detail ?? "No forwarding line is available.")
  }
  return { session, agent: activated.agent }
}

export async function fetchAgent(session: Session): Promise<Agent> {
  const { agent } = await request<{ agent: Agent }>(`/onboarding/sessions/${session.tenantId}`, {}, session.token)
  return agent
}

export async function confirmForwarding(session: Session): Promise<Agent> {
  const { agent } = await request<{ agent: Agent }>(
    `/onboarding/sessions/${session.tenantId}/voicemail-forwarding`,
    { method: "POST", body: JSON.stringify({ confirmed: true }) },
    session.token,
  )
  return agent
}

export async function fetchCalls(session: Session): Promise<ScreenedCall[]> {
  const { calls } = await request<{ calls: ScreenedCall[] }>(
    `/onboarding/sessions/${session.tenantId}/calls`,
    {},
    session.token,
  )
  return calls
}
