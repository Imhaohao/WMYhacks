import type { Session } from "@/lib/api"

const KEY = "gotchu-session"

export function loadSession(): Session | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? "null") as Session | null
    return parsed?.tenantId && parsed?.token ? parsed : null
  } catch {
    return null
  }
}

export function saveSession(session: Session): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(session))
  } catch {
    /* private mode: the session lives for this tab only */
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(KEY)
  } catch {
    /* nothing stored */
  }
}
