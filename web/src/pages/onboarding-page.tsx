import { useEffect, useState, type FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"
import { ArrowRight, Check, Copy, WarningCircle } from "@phosphor-icons/react"
import { SiteHeader } from "@/components/site-header"
import { Button } from "@/components/ui/button"
import { Field } from "@/components/ui/field"
import { ApiError, agentLine, confirmForwarding, fetchAgent, signUp, type Agent } from "@/lib/api"
import {
  CARRIER_LABELS,
  formatPhone,
  forwardingCodes,
  isLikelyPhone,
  telHref,
  toE164,
  type Carrier,
} from "@/lib/phone"
import { clearSession, loadSession, saveSession } from "@/lib/session"
import { cn } from "@/lib/utils"

function signUpErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 503) {
    return "Sign-ups are paused on this server right now. Please try again later."
  }
  if (error instanceof ApiError && error.status === 409) {
    return "Your details are saved, but no forwarding line is free right now. Please try again later."
  }
  return "We couldn't reach Gotchu. Check your connection and try again."
}

function ErrorNote({ message }: { message: string }) {
  return (
    <p role="alert" className="flex items-start gap-2 rounded-panel-inner bg-urgent-soft px-4 py-3 text-base text-urgent">
      <WarningCircle weight="fill" className="mt-0.5 size-5 shrink-0" aria-hidden />
      {message}
    </p>
  )
}

function SignUpStep({ onDone }: { onDone: (agent: Agent) => void }) {
  const [name, setName] = useState("")
  const [phone, setPhone] = useState("")
  const [email, setEmail] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!isLikelyPhone(phone)) {
      setError("Enter your mobile number with its area code, like (415) 555-0143.")
      return
    }
    setBusy(true)
    setError(null)
    try {
      const { session, agent } = await signUp({
        display_name: name.trim(),
        owner_phone: toE164(phone),
        owner_email: email.trim(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      })
      saveSession(session)
      onDone(agent)
    } catch (caught) {
      setError(signUpErrorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-6">
      <div>
        <h1 className="text-title">Set up Gotchu</h1>
        <p className="text-lead mt-3">Tell Gotchu who it answers for and where to text you.</p>
      </div>
      <Field label="Your name" autoComplete="name" required value={name} onChange={(e) => setName(e.target.value)} />
      <Field
        label="Your mobile number"
        type="tel"
        autoComplete="tel"
        inputMode="tel"
        required
        value={phone}
        onChange={(e) => setPhone(e.target.value)}
        hint="Gotchu texts you here after every call it screens."
      />
      <Field
        label="Email"
        type="email"
        autoComplete="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        hint="A copy of each message goes here too."
      />
      {error && <ErrorNote message={error} />}
      <Button type="submit" size="lg" disabled={busy} className="justify-self-start">
        {busy ? "Setting up your line" : "Get my forwarding number"}
        {!busy && <ArrowRight weight="bold" className="size-4" aria-hidden />}
      </Button>
    </form>
  )
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }
  return (
    <Button type="button" variant="secondary" size="sm" onClick={copy} aria-label={`Copy ${label}`}>
      {copied ? <Check weight="bold" className="size-4 text-accent" aria-hidden /> : <Copy weight="bold" className="size-4" aria-hidden />}
      {copied ? "Copied" : "Copy"}
    </Button>
  )
}

function CarrierPicker({ carrier, onChange }: { carrier: Carrier; onChange: (next: Carrier) => void }) {
  return (
    <div role="radiogroup" aria-label="Your carrier" className="inline-flex rounded-control bg-sunken p-1">
      {(Object.keys(CARRIER_LABELS) as Carrier[]).map((option) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={carrier === option}
          onClick={() => onChange(option)}
          className={cn(
            "rounded-control-inner px-3 py-2 text-sm font-semibold text-muted transition-colors focus-visible:outline-2 focus-visible:outline-accent",
            carrier === option && "bg-raised text-ink shadow-control",
          )}
        >
          {CARRIER_LABELS[option]}
        </button>
      ))}
    </div>
  )
}

function CodeRow({ label, code }: { label: string; code: string }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 py-4">
      <div>
        <p className="text-sm text-muted">{label}</p>
        <p className="figures mt-1 text-xl font-bold">{code}</p>
      </div>
      <div className="flex gap-2">
        <CopyButton value={code} label={label.toLowerCase()} />
        <Button asChild variant="secondary" size="sm" className="sm:hidden">
          <a href={telHref(code)}>Dial</a>
        </Button>
      </div>
    </div>
  )
}

function ForwardStep({ agent, onConfirmed }: { agent: Agent; onConfirmed: () => void }) {
  const [carrier, setCarrier] = useState<Carrier>("gsm")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const line = agentLine(agent)
  const codes = forwardingCodes(carrier, line)

  const confirm = async () => {
    const session = loadSession()
    if (!session) return
    setBusy(true)
    setError(null)
    try {
      await confirmForwarding(session)
      onConfirmed()
    } catch {
      setError("We couldn't save that. Check your connection and try again.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid gap-8">
      <div>
        <h1 className="text-title">Forward missed calls to Gotchu</h1>
        <p className="text-lead mt-3">
          Your Gotchu line is <span className="figures font-semibold text-ink">{formatPhone(line)}</span>. Dial the
          code below from {formatPhone(agent.owner_phone)} so calls you don&apos;t answer go there.
        </p>
      </div>
      <div className="grid gap-4">
        <CarrierPicker carrier={carrier} onChange={setCarrier} />
        <div className="divide-y divide-line rounded-panel bg-raised px-5 shadow-panel">
          <CodeRow label="Turn on" code={codes.on} />
          <CodeRow label="Turn off any time" code={codes.off} />
        </div>
      </div>
      {error && <ErrorNote message={error} />}
      <div className="flex flex-wrap items-center gap-3">
        <Button size="lg" onClick={confirm} disabled={busy}>
          I dialed it
          <ArrowRight weight="bold" className="size-4" aria-hidden />
        </Button>
        <Button asChild variant="quiet" size="lg">
          <Link to="/calls">I&apos;ll do it later</Link>
        </Button>
      </div>
    </div>
  )
}

function useResumedAgent(): [Agent | null, (agent: Agent) => void, boolean] {
  const [agent, setAgent] = useState<Agent | null>(null)
  const [checking, setChecking] = useState(() => Boolean(loadSession()))

  useEffect(() => {
    const session = loadSession()
    if (!session) return
    fetchAgent(session)
      .then(setAgent)
      .catch(() => clearSession())
      .finally(() => setChecking(false))
  }, [])

  return [agent, setAgent, checking]
}

export function OnboardingPage() {
  const navigate = useNavigate()
  const [agent, setAgent, checking] = useResumedAgent()
  const hasLine = Boolean(agent && agentLine(agent))

  return (
    <div className="min-h-[100dvh]">
      <SiteHeader />
      <main className="mx-auto w-full max-w-xl px-4 pb-24 pt-8 sm:px-6 sm:pt-14">
        {!checking && !hasLine && <SignUpStep onDone={setAgent} />}
        {agent && hasLine && <ForwardStep agent={agent} onConfirmed={() => navigate("/calls")} />}
      </main>
    </div>
  )
}
