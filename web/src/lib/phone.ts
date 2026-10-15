export function toE164(input: string): string {
  const digits = input.replace(/\D/g, "")
  if (digits.length === 10) return `+1${digits}`
  return digits ? `+${digits}` : ""
}

export function isLikelyPhone(input: string): boolean {
  const digits = toE164(input).slice(1)
  return digits.length >= 11 && digits.length <= 15
}

export function formatPhone(e164: string): string {
  const match = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(e164)
  return match ? `(${match[1]}) ${match[2]}-${match[3]}` : e164
}

export type Carrier = "gsm" | "verizon"

export const CARRIER_LABELS: Record<Carrier, string> = {
  gsm: "AT&T, T-Mobile and most others",
  verizon: "Verizon",
}

export function forwardingCodes(carrier: Carrier, line: string): { on: string; off: string } {
  if (carrier === "verizon") return { on: `*71${line.replace(/^\+1/, "")}`, off: "*73" }
  return { on: `**61*${line}#`, off: "##61#" }
}

export function telHref(code: string): string {
  return `tel:${code.replace(/#/g, "%23")}`
}
