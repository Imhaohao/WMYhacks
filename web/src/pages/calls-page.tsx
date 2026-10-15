import { useEffect, useState } from "react"
import { Link, Navigate } from "react-router-dom"
import { PhoneIncoming, WarningCircle } from "@phosphor-icons/react"
import { ScreenedCallCard } from "@/components/screened-call-card"
import { SiteHeader } from "@/components/site-header"
import { Button } from "@/components/ui/button"
import { ApiError, agentLine, fetchAgent, fetchCalls, type Agent, type ScreenedCall, type Session } from "@/lib/api"
import { formatPhone } from "@/lib/phone"
import { clearSession, loadSession } from "@/lib/session"

const POLL_MS = 5000

type Inbox =
  | { state: "loading" }
  | { state: "lost" }
  | { state: "offline"; calls: ScreenedCall[] }
  | { state: "ready"; calls: ScreenedCall[] }

function useInbox(session: Session): [Inbox, Agent | null] {
  const [inbox, setInbox] = useState<Inbox>({ state: "loading" })
  const [agent, setAgent] = useState<Agent | null>(null)

  useEffect(() => {
    let cancelled = false
    const refresh = async () => {
      try {
        const [nextAgent, calls] = await Promise.all([fetchAgent(session), fetchCalls(session)])
        if (cancelled) return
        setAgent(nextAgent)
        setInbox({ state: "ready", calls })
      } catch (error) {
        if (cancelled) return
        const lost = error instanceof ApiError && (error.status === 401 || error.status === 403)
        setInbox((current) =>
          lost ? { state: "lost" } : { state: "offline", calls: "calls" in current ? current.calls : [] },
        )
      }
    }
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [session])

  return [inbox, agent]
}

function LineFacts({ agent }: { agent: Agent }) {
  return (
    <dl className="figures mt-5 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-base lg:grid-cols-1 lg:gap-x-0">
      <dt className="text-muted">Gotchu line</dt>
      <dd className="font-semibold lg:mb-3">{formatPhone(agentLine(agent))}</dd>
      <dt className="text-muted">Texts go to</dt>
      <dd className="font-semibold lg:mb-3">{formatPhone(agent.owner_phone)}</dd>
    </dl>
  )
}

function EmptyInbox({ agent }: { agent: Agent | null }) {
  return (
    <div className="grid justify-items-start gap-4 rounded-panel bg-raised p-6 shadow-panel sm:p-8">
      <span className="grid size-12 place-items-center rounded-full bg-sunken text-muted">
        <PhoneIncoming weight="bold" className="size-6" aria-hidden />
      </span>
      <h2 className="text-xl font-bold">No screened calls yet</h2>
      <p className="max-w-prose text-base leading-relaxed text-muted">
        Call {agent ? formatPhone(agent.owner_phone) : "your mobile"} from another phone and let it ring out. Talk to
        Gotchu like a caller would, hang up, and the call shows up here within a minute.
      </p>
      {agent && !agent.voicemail_forwarding_confirmed && (
        <Button asChild variant="secondary">
          <Link to="/onboarding">Show my forwarding code</Link>
        </Button>
      )}
    </div>
  )
}

function SkeletonCard() {
  return (
    <div aria-hidden className="h-64 animate-pulse rounded-panel bg-raised shadow-panel motion-reduce:animate-none" />
  )
}

function LostSession() {
  const startOver = () => clearSession()
  return (
    <div className="grid justify-items-start gap-4">
      <h1 className="text-title">This browser lost its link to your line</h1>
      <p className="text-lead">Set up again to get a fresh link. Calls already screened still reached your phone by text.</p>
      <Button asChild onClick={startOver}>
        <Link to="/onboarding">Set up again</Link>
      </Button>
    </div>
  )
}

function CallList({ inbox, agent }: { inbox: Inbox; agent: Agent | null }) {
  if (inbox.state === "loading") return <SkeletonCard />
  if (inbox.state === "lost") return null
  return (
    <div className="grid gap-5">
      {inbox.state === "offline" && (
        <p role="status" className="flex items-center gap-2 text-base text-urgent">
          <WarningCircle weight="fill" className="size-5" aria-hidden />
          Can&apos;t reach Gotchu right now. Retrying every few seconds.
        </p>
      )}
      {inbox.calls.length === 0 ? (
        <EmptyInbox agent={agent} />
      ) : (
        inbox.calls.map((call) => <ScreenedCallCard key={call.id} call={call} />)
      )}
    </div>
  )
}

function Inbox({ session }: { session: Session }) {
  const [inbox, agent] = useInbox(session)
  if (inbox.state === "lost") return <LostSession />
  return (
    <div className="grid gap-10 lg:grid-cols-[16rem_1fr] lg:gap-16">
      <div className="lg:sticky lg:top-8 lg:self-start">
        <h1 className="text-title">Your screened calls</h1>
        {agent && <LineFacts agent={agent} />}
      </div>
      <div aria-live="polite" className="max-w-3xl">
        <CallList inbox={inbox} agent={agent} />
      </div>
    </div>
  )
}

export function CallsPage() {
  const [session] = useState(loadSession)
  if (!session) return <Navigate to="/onboarding" replace />
  return (
    <div className="min-h-[100dvh]">
      <SiteHeader />
      <main className="mx-auto w-full max-w-6xl px-4 pb-24 pt-8 sm:px-6 sm:pt-14">
        <Inbox session={session} />
      </main>
    </div>
  )
}
