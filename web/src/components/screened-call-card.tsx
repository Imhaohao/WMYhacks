import { CheckCircle, Phone, PhoneOutgoing, WarningCircle } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import type { ScreenedCall } from "@/lib/api"
import { formatPhone } from "@/lib/phone"
import { cn } from "@/lib/utils"

const timeFormat = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" })
const dayFormat = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" })

function receivedLabel(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ""
  const sameDay = date.toDateString() === new Date().toDateString()
  return sameDay ? timeFormat.format(date) : `${dayFormat.format(date)}, ${timeFormat.format(date)}`
}

function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
  return letters.join("") || "?"
}

function firstName(name: string): string {
  return name === "Unknown caller" ? "back" : name.split(/\s+/)[0]
}

function callbackSentence(call: ScreenedCall): string {
  const number = call.callback_number && call.callback_number !== call.caller_number ? call.callback_number : ""
  const at = number ? ` at ${formatPhone(number)}` : ""
  const when = call.callback_time ? ` ${call.callback_time}` : ""
  return `Wants a call back${when}${at}`
}

function UrgentMark() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-urgent-soft px-2.5 py-1 text-sm font-semibold text-urgent">
      <WarningCircle weight="fill" className="size-4" aria-hidden />
      Urgent
    </span>
  )
}

type ScreenedCallCardProps = {
  call: ScreenedCall
  className?: string
}

export function ScreenedCallCard({ call, className }: ScreenedCallCardProps) {
  const name = call.caller_name || "Unknown caller"
  const callbackNumber = call.callback_number || call.caller_number
  return (
    <article
      aria-label={`Screened call from ${name}`}
      className={cn("rounded-panel bg-raised p-5 shadow-panel sm:p-6", className)}
    >
      <header className="flex items-start gap-3">
        <span
          aria-hidden
          className="grid size-11 shrink-0 place-items-center rounded-full bg-accent-soft font-bold text-accent"
        >
          {initials(name)}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-lg font-bold">{name}</h3>
          <p className="figures flex flex-wrap gap-x-3 text-sm text-muted">
            <span>{call.caller_number ? formatPhone(call.caller_number) : "Number withheld"}</span>
            <time dateTime={call.received_at}>{receivedLabel(call.received_at)}</time>
          </p>
        </div>
        {call.urgency === "urgent" && <UrgentMark />}
      </header>

      <p className="mt-5 text-xl font-semibold leading-snug sm:text-2xl">
        {call.reason || "Didn't say why they called"}
      </p>

      {call.wants_callback && (
        <p className="mt-3 flex items-center gap-2 text-base font-medium text-accent">
          <PhoneOutgoing weight="bold" className="size-4 shrink-0" aria-hidden />
          {callbackSentence(call)}
        </p>
      )}

      {call.caller_words && (
        <blockquote className="mt-4 rounded-panel-inner bg-sunken px-4 py-3 text-base leading-relaxed text-muted">
          {call.caller_words}
        </blockquote>
      )}

      <footer className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <p className="flex items-center gap-1.5 text-sm text-muted">
          <CheckCircle weight="fill" className="size-4 text-accent" aria-hidden />
          {call.texted ? "Texted to you" : "Saved here"}
        </p>
        {callbackNumber && (
          <Button asChild size="sm">
            <a href={`tel:${callbackNumber}`}>
              <Phone weight="bold" className="size-4" aria-hidden />
              Call {firstName(name)}
            </a>
          </Button>
        )}
      </footer>
    </article>
  )
}
