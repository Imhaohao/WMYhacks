import { useCallback, useEffect, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import {
  ArrowLeft,
  ArrowRight,
  CalendarClock,
  Check,
  CheckCircle2,
  Loader2,
  Mail,
  MessageSquareText,
  PlugZap,
  RefreshCw,
  Send,
  Upload,
} from "lucide-react"
import { SiteHeader } from "@/components/site-header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import {
  disconnectCalendar,
  disconnectGmail,
  fetchCalendarStatus,
  fetchGmailStatus,
  fetchHealth,
  fetchBridgeSpecs,
  fetchOwnerConfig,
  fetchServices,
  getCalendarAuthUrl,
  getGmailAuthUrl,
  saveOwnerConfig,
  syncMessageContext,
  uploadContacts,
  type CalendarStatus,
  type GmailStatus,
  type HealthCheck,
  type OwnerConfig,
  type BridgeSpecsResult,
  type ServiceStatus,
} from "@/lib/api"

const STEPS = ["You", "Contacts", "Context", "Calendar", "Services", "Try"]
type McpService = "gmail" | "calendar" | "messages"

export function SetupPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [config, setConfig] = useState<OwnerConfig | null>(null)
  const [health, setHealth] = useState<HealthCheck | null>(null)
  const [services, setServices] = useState<ServiceStatus | null>(null)
  const [uploadSummary, setUploadSummary] = useState<string | null>(null)
  const [sampleNames, setSampleNames] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editingSummary, setEditingSummary] = useState(false)
  const [summaryDraft, setSummaryDraft] = useState("")
  const [syncing, setSyncing] = useState(false)
  const [mcpHelp, setMcpHelp] = useState<McpService | null>(null)
  const [bridgeSpecs, setBridgeSpecs] = useState<BridgeSpecsResult | null>(null)
  const [bridgeLoading, setBridgeLoading] = useState<"gmail" | "calendar" | null>(null)
  const [calStatus, setCalStatus] = useState<CalendarStatus | null>(null)
  const [connectingCal, setConnectingCal] = useState(false)
  const [gmailStatus, setGmailStatus] = useState<GmailStatus | null>(null)
  const [connectingGmail, setConnectingGmail] = useState(false)

  useEffect(() => {
    fetchOwnerConfig()
      .then(setConfig)
      .catch(() =>
        setConfig({
          display_name: "",
          owner_phone: "",
          owner_email: "",
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          manual_availability: "",
          setup_complete: false,
          message_context_summary: "",
          message_context_source: "manual",
          message_context_synced_at: "",
        }),
      )
  }, [])

  const refreshHealth = useCallback(async () => {
    try {
      const [h, s, c, g] = await Promise.all([
        fetchHealth(),
        fetchServices(),
        fetchCalendarStatus(),
        fetchGmailStatus(),
      ])
      setHealth(h)
      setServices(s)
      setCalStatus(c)
      setGmailStatus(g)
      setSampleNames(h.contacts.sample_names)
      setUploadSummary(h.contacts.summary)
    } catch {
      setError("Could not reach onboarding API. Start it with: uv run onboarding_api.py")
    }
  }, [])

  const connectGoogleGmail = async () => {
    setConnectingGmail(true)
    setError(null)
    try {
      const { ok, url, detail } = await getGmailAuthUrl()
      if (!ok || !url) {
        setError(detail ?? "Gmail OAuth isn't configured on the server yet.")
        return
      }
      const popup = window.open(url, "gmail-oauth", "width=520,height=680")
      await new Promise<void>((resolve) => {
        let elapsed = 0
        const id = window.setInterval(async () => {
          elapsed += 1500
          try {
            const s = await fetchGmailStatus()
            setGmailStatus(s)
            if (s.connected) {
              window.clearInterval(id)
              resolve()
              return
            }
          } catch {
            /* keep polling */
          }
          if ((popup && popup.closed) || elapsed > 180000) {
            window.clearInterval(id)
            resolve()
          }
        }, 1500)
      })
      setServices(await fetchServices())
    } catch {
      setError("Could not start the Gmail connection.")
    } finally {
      setConnectingGmail(false)
    }
  }

  const disconnectGoogleGmail = async () => {
    setError(null)
    try {
      await disconnectGmail()
      setGmailStatus(await fetchGmailStatus())
      setServices(await fetchServices())
    } catch {
      setError("Could not disconnect Gmail.")
    }
  }

  const connectGoogleCalendar = async () => {
    setConnectingCal(true)
    setError(null)
    try {
      const { ok, url, detail } = await getCalendarAuthUrl()
      if (!ok || !url) {
        setError(detail ?? "Google OAuth isn't configured on the server yet.")
        return
      }
      const popup = window.open(url, "gcal-oauth", "width=520,height=680")
      // The popup lands on the backend callback, which stores the token and
      // closes itself. We can't read across origins, so poll status until it
      // flips to connected (or the popup closes / we time out).
      await new Promise<void>((resolve) => {
        let elapsed = 0
        const id = window.setInterval(async () => {
          elapsed += 1500
          try {
            const s = await fetchCalendarStatus()
            setCalStatus(s)
            if (s.connected) {
              window.clearInterval(id)
              resolve()
              return
            }
          } catch {
            /* keep polling */
          }
          if ((popup && popup.closed) || elapsed > 180000) {
            window.clearInterval(id)
            resolve()
          }
        }, 1500)
      })
      setServices(await fetchServices())
    } catch {
      setError("Could not start the Google Calendar connection.")
    } finally {
      setConnectingCal(false)
    }
  }

  const disconnectGoogleCalendar = async () => {
    setError(null)
    try {
      await disconnectCalendar()
      setCalStatus(await fetchCalendarStatus())
      setServices(await fetchServices())
    } catch {
      setError("Could not disconnect the calendar.")
    }
  }

  useEffect(() => {
    if (step < 2) return
    const id = window.setTimeout(() => void refreshHealth(), 0)
    return () => window.clearTimeout(id)
  }, [step, refreshHealth])

  const progress = ((step + 1) / STEPS.length) * 100

  const persist = async (patch: Partial<OwnerConfig>, next?: number) => {
    if (!config) return
    setSaving(true)
    setError(null)
    try {
      const updated = await saveOwnerConfig({ ...config, ...patch })
      setConfig(updated)
      if (next !== undefined) setStep(next)
    } catch {
      setError("Failed to save. Is the API running?")
    } finally {
      setSaving(false)
    }
  }

  const onVcfUpload = async (file: File) => {
    setSaving(true)
    setError(null)
    try {
      const result = await uploadContacts(file)
      setUploadSummary(result.summary)
      setSampleNames(result.sample_names)
    } catch {
      setError("Upload failed")
    } finally {
      setSaving(false)
    }
  }

  const onSyncMessages = async () => {
    if (!config) return
    setSyncing(true)
    setError(null)
    try {
      const ctx = await syncMessageContext()
      setConfig({ ...config, ...ctx })
      setServices(await fetchServices())
      setEditingSummary(false)
    } catch {
      setError("Sync failed. Is the onboarding API running?")
    } finally {
      setSyncing(false)
    }
  }

  const mcpConnectUrl = (kind: McpService) => {
    if (kind === "gmail") return import.meta.env.VITE_GMAIL_MCP_CONNECT_URL
    if (kind === "calendar") return import.meta.env.VITE_CALENDAR_MCP_CONNECT_URL
    return import.meta.env.VITE_MESSAGES_MCP_CONNECT_URL
  }

  const showMcpHelp = (kind: McpService) => {
    setBridgeSpecs(null)
    setMcpHelp(kind)
  }

  const connectMcp = (kind: McpService) => {
    const url = mcpConnectUrl(kind)
    if (url) window.open(url, "_blank", "noopener,noreferrer")
    showMcpHelp(kind)
  }

  const emitBridgeSpecs = async (kind: "gmail" | "calendar") => {
    setBridgeLoading(kind)
    setError(null)
    try {
      setBridgeSpecs(await fetchBridgeSpecs(kind))
      setMcpHelp(null)
      setServices(await fetchServices())
    } catch {
      setError("Could not read the MCP outbox. Is the onboarding API running?")
    } finally {
      setBridgeLoading(null)
    }
  }

  const copyBridgeSpecs = async () => {
    if (!bridgeSpecs) return
    await navigator.clipboard.writeText(JSON.stringify(bridgeSpecs.specs, null, 2))
  }

  const saveSummaryEdit = async () => {
    // A manual edit reclaims the summary as owner-authored so the next sync
    // won't silently overwrite it.
    await persist({ message_context_summary: summaryDraft, message_context_source: "manual" })
    setEditingSummary(false)
  }

  const finishSetup = async () => {
    await persist({ setup_complete: true })
    navigate("/try")
  }

  const statusPill = (ok: boolean, text: string) => (
    <span
      className={`inline-flex min-h-7 items-center rounded-full px-3 text-xs font-semibold ${
        ok ? "bg-sage-100 text-sage-800" : "bg-amber-100 text-amber-800"
      }`}
    >
      {text}
    </span>
  )

  if (!config) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="size-8 animate-spin text-sage-600" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-wash">
      <SiteHeader />
      <div className="mx-auto max-w-2xl px-6 py-10">
        <Link
          to="/"
          className="mb-6 inline-flex items-center gap-1 text-sm text-ink/50 hover:text-ink"
        >
          <ArrowLeft className="size-4" /> Back home
        </Link>

        <h1 className="text-3xl font-semibold">Set up your agent</h1>
        <p className="mt-2 text-ink/60">Step {step + 1} of {STEPS.length} — {STEPS[step]}</p>
        <Progress value={progress} className="mt-6" />

        {error && (
          <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <Card className="mt-8">
          {step === 0 && (
            <>
              <CardHeader>
                <CardTitle>About you</CardTitle>
                <CardDescription>The agent speaks on your behalf and notifies you after each call.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <Label htmlFor="name">Display name</Label>
                  <Input
                    id="name"
                    placeholder="Alex"
                    value={config.display_name}
                    onChange={(e) => setConfig({ ...config, display_name: e.target.value })}
                  />
                </div>
                <div>
                  <Label htmlFor="phone">Your phone (E.164)</Label>
                  <Input
                    id="phone"
                    placeholder="+14155551234"
                    value={config.owner_phone}
                    onChange={(e) => setConfig({ ...config, owner_phone: e.target.value })}
                  />
                </div>
                <div>
                  <Label htmlFor="email">Your email</Label>
                  <Input
                    id="email"
                    type="email"
                    placeholder="you@example.com"
                    value={config.owner_email}
                    onChange={(e) => setConfig({ ...config, owner_email: e.target.value })}
                  />
                </div>
                <Button
                  className="w-full"
                  disabled={!config.display_name || !config.owner_phone || !config.owner_email || saving}
                  onClick={() =>
                    void persist(
                      {
                        display_name: config.display_name,
                        owner_phone: config.owner_phone,
                        owner_email: config.owner_email,
                        timezone: config.timezone,
                      },
                      1,
                    )
                  }
                >
                  Continue <ArrowRight className="size-4" />
                </Button>
              </CardContent>
            </>
          )}

          {step === 1 && (
            <>
              <CardHeader>
                <CardTitle>Import contacts</CardTitle>
                <CardDescription>
                  Upload a vCard so the agent recognizes callers by name. Names only — numbers stay local.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <label className="flex cursor-pointer flex-col items-center rounded-2xl border-2 border-dashed border-ink/20 bg-canvas px-6 py-10 transition hover:border-sage-400 hover:bg-sage-50/60">
                  <Upload className="size-8 text-sage-600" />
                  <span className="mt-3 font-medium">Drop contacts.vcf here</span>
                  <span className="mt-1 text-sm text-ink/50">or click to browse</span>
                  <input
                    type="file"
                    accept=".vcf,.vcard"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      if (f) void onVcfUpload(f)
                    }}
                  />
                </label>
                {uploadSummary && (
                  <div className="rounded-xl bg-sage-50 px-4 py-3 text-sm text-sage-900">
                    <CheckCircle2 className="mb-1 inline size-4" /> {uploadSummary}
                    {sampleNames.length > 0 && (
                      <p className="mt-1 text-sage-700">e.g. {sampleNames.slice(0, 5).join(", ")}</p>
                    )}
                  </div>
                )}
                <div className="flex gap-3">
                  <Button variant="secondary" onClick={() => setStep(0)}>
                    Back
                  </Button>
                  <Button className="flex-1" onClick={() => setStep(2)}>
                    {uploadSummary ? "Continue" : "Skip for now"}
                  </Button>
                </div>
              </CardContent>
            </>
          )}

          {step === 2 && (
            <>
              <CardHeader>
                <CardTitle>Connect recent context</CardTitle>
                <CardDescription>
                  Connect a Messages MCP source first. The agent only receives a short redacted
                  summary: topics and urgency, never full chat logs.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="rounded-xl border border-ink/10 bg-canvas px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 gap-3">
                      <MessageSquareText className="mt-0.5 size-5 shrink-0 text-sage-700" />
                      <div className="min-w-0">
                        <p className="font-semibold">Messages MCP</p>
                        <p className="mt-1 text-sm text-ink/55">
                          Use OAuth in your MCP client to connect the account that has recent
                          message context.
                        </p>
                      </div>
                    </div>
                    {statusPill(Boolean(config.message_context_summary), config.message_context_summary ? "Ready" : "Connect")}
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button variant="secondary" onClick={() => connectMcp("messages")}>
                      <PlugZap className="size-4" /> Connect Messages MCP
                    </Button>
                    <Button variant="secondary" disabled={syncing} onClick={() => void onSyncMessages()}>
                      {syncing ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <MessageSquareText className="size-4" />
                      )}
                      Sync context
                    </Button>
                  </div>
                </div>
                {mcpHelp === "messages" && (
                  <div className="rounded-xl border border-dusk-200 bg-dusk-50 px-4 py-3 text-sm text-dusk-900">
                    <p className="font-semibold">Messages MCP connection</p>
                    <p className="mt-1">
                      Complete the OAuth flow in your MCP client, then use Sync context. The
                      backend stores only the redacted summary for the voice agent.
                    </p>
                  </div>
                )}
                {editingSummary ? (
                  <div className="space-y-2">
                    <Label htmlFor="summary">Edit summary</Label>
                    <textarea
                      id="summary"
                      rows={4}
                      placeholder="e.g. Sarah texted about the lease — waiting on my reply. Do not paste full chat logs."
                      className="flex w-full rounded-xl border border-ink/15 bg-parchment px-4 py-2 text-sm shadow-sm transition-colors placeholder:text-ink/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sage-500"
                      value={summaryDraft}
                      onChange={(e) => setSummaryDraft(e.target.value)}
                    />
                    <div className="flex gap-3">
                      <Button variant="secondary" onClick={() => setEditingSummary(false)}>
                        Cancel
                      </Button>
                      <Button className="flex-1" disabled={saving} onClick={() => void saveSummaryEdit()}>
                        Save summary
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="rounded-xl border border-ink/10 bg-canvas px-4 py-3 text-sm">
                      {config.message_context_summary ? (
                        <p className="text-ink/70">{config.message_context_summary}</p>
                      ) : (
                        <p className="text-ink/40">
                          No summary yet. Sync from Messages or add one manually.
                        </p>
                      )}
                      <p className="mt-2 text-xs text-ink/50">
                        {config.message_context_source === "imessage" ? "Synced from Messages" : "Owner-provided"}
                        {config.message_context_synced_at &&
                          ` · last synced ${new Date(config.message_context_synced_at).toLocaleString()}`}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-3">
                      <Button variant="secondary" onClick={() => connectMcp("messages")}>
                        <PlugZap className="size-4" /> Connect MCP
                      </Button>
                      <Button variant="secondary" disabled={syncing} onClick={() => void onSyncMessages()}>
                        {syncing ? <Loader2 className="size-4 animate-spin" /> : null} Sync from Messages
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={() => {
                          setSummaryDraft(config.message_context_summary)
                          setEditingSummary(true)
                        }}
                      >
                        Edit summary
                      </Button>
                    </div>
                  </>
                )}
                {!editingSummary && (
                  <div className="flex gap-3">
                    <Button variant="secondary" onClick={() => setStep(1)}>
                      Back
                    </Button>
                    <Button className="flex-1" onClick={() => setStep(3)}>
                      Continue
                    </Button>
                  </div>
                )}
              </CardContent>
            </>
          )}

          {step === 3 && (
            <>
              <CardHeader>
                <CardTitle>Connect calendar</CardTitle>
                <CardDescription>
                  Connect your Google Calendar so the agent can check your real availability and
                  drop tentative callback events straight onto your calendar.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="rounded-xl border border-ink/10 bg-canvas px-4 py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 gap-3">
                      <CalendarClock className="mt-0.5 size-5 shrink-0 text-mauve-500" />
                      <div className="min-w-0">
                        <p className="font-semibold">Google Calendar</p>
                        <p className="mt-1 text-sm text-ink/55">
                          {calStatus?.connected
                            ? `Connected as ${calStatus.email ?? "your Google account"}.`
                            : calStatus && !calStatus.configured
                              ? "Server is missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — add them to server/.env, then reconnect."
                              : "Authorize your Google account for live availability and callback bookings."}
                        </p>
                        <p className="mt-2 truncate text-xs text-ink/45">
                          {config.timezone || services?.calendar.timezone || "America/Los_Angeles"}
                        </p>
                      </div>
                    </div>
                    {statusPill(
                      Boolean(calStatus?.connected),
                      calStatus?.connected ? "Connected" : "Connect",
                    )}
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      disabled={connectingCal}
                      onClick={() => void connectGoogleCalendar()}
                    >
                      {connectingCal ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <PlugZap className="size-4" />
                      )}
                      {calStatus?.connected ? "Reconnect" : "Connect Google Calendar"}
                    </Button>
                    {calStatus?.connected && (
                      <Button variant="secondary" onClick={() => void disconnectGoogleCalendar()}>
                        Disconnect
                      </Button>
                    )}
                  </div>
                </div>
                <div>
                  <Label htmlFor="availability">Manual availability</Label>
                  <Input
                    id="availability"
                    placeholder="Weekdays 2–5pm PT"
                    value={config.manual_availability}
                    onChange={(e) => setConfig({ ...config, manual_availability: e.target.value })}
                  />
                </div>
                <div className="flex gap-3">
                  <Button variant="secondary" onClick={() => setStep(2)}>
                    Back
                  </Button>
                  <Button
                    className="flex-1"
                    disabled={saving}
                    onClick={() => void persist({ manual_availability: config.manual_availability }, 4)}
                  >
                    Continue
                  </Button>
                </div>
              </CardContent>
            </>
          )}

          {step === 4 && (
            <>
              <CardHeader>
                <CardTitle>Connect services</CardTitle>
                <CardDescription>
                  Gmail and Calendar can connect directly with Google OAuth. Messages sync as a
                  redacted summary only.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {services ? (
                  <>
                    <div className="space-y-3">
                      <div className="rounded-xl border border-ink/10 bg-canvas px-4 py-4">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex min-w-0 gap-3">
                            <Mail className="mt-0.5 size-5 shrink-0 text-dusk-700" />
                            <div className="min-w-0">
                              <p className="font-semibold">Gmail</p>
                              <p className="mt-1 text-sm text-ink/55">
                                {gmailStatus?.connected
                                  ? `Connected as ${gmailStatus.email ?? "your Google account"}.`
                                  : gmailStatus && !gmailStatus.configured
                                    ? "Server is missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — add them to server/.env, then reconnect."
                                    : services.gmail.detail}
                              </p>
                              <p className="mt-2 truncate text-xs text-ink/45">
                                {services.gmail.target || config.owner_email || "No owner email saved"}
                              </p>
                            </div>
                          </div>
                          {statusPill(
                            Boolean(gmailStatus?.connected ?? services.gmail.ok),
                            gmailStatus?.connected
                              ? "Connected"
                              : services.gmail.mode === "smtp"
                                ? "SMTP"
                                : "Connect",
                          )}
                        </div>
                        {services.gmail.pending_actions > 0 && (
                          <p className="mt-3 text-xs font-medium text-amber-700">
                            {services.gmail.pending_actions} draft action pending
                          </p>
                        )}
                        <div className="mt-4 flex flex-wrap gap-2">
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={connectingGmail}
                            onClick={() => void connectGoogleGmail()}
                          >
                            {connectingGmail ? (
                              <Loader2 className="size-4 animate-spin" />
                            ) : (
                              <PlugZap className="size-4" />
                            )}
                            {gmailStatus?.connected ? "Reconnect Gmail" : "Connect Gmail"}
                          </Button>
                          {gmailStatus?.connected && (
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => void disconnectGoogleGmail()}
                            >
                              Disconnect
                            </Button>
                          )}
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={bridgeLoading === "gmail" || services.gmail.pending_actions === 0}
                            onClick={() => void emitBridgeSpecs("gmail")}
                          >
                            {bridgeLoading === "gmail" ? (
                              <Loader2 className="size-4 animate-spin" />
                            ) : (
                              <Send className="size-4" />
                            )}
                            Emit drafts
                          </Button>
                        </div>
                      </div>

                      <div className="rounded-xl border border-ink/10 bg-canvas px-4 py-4">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex min-w-0 gap-3">
                            <CalendarClock className="mt-0.5 size-5 shrink-0 text-mauve-500" />
                            <div className="min-w-0">
                              <p className="font-semibold">Calendar</p>
                              <p className="mt-1 text-sm text-ink/55">{services.calendar.detail}</p>
                              <p className="mt-2 truncate text-xs text-ink/45">
                                {services.calendar.timezone} · {services.calendar.target || "No calendar target"}
                              </p>
                            </div>
                          </div>
                          {statusPill(
                            Boolean(calStatus?.connected ?? services.calendar.ok),
                            calStatus?.connected ?? services.calendar.ok ? "Connected" : "Connect",
                          )}
                        </div>
                        <div className="mt-4 flex flex-wrap gap-2">
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={connectingCal}
                            onClick={() => void connectGoogleCalendar()}
                          >
                            {connectingCal ? (
                              <Loader2 className="size-4 animate-spin" />
                            ) : (
                              <PlugZap className="size-4" />
                            )}
                            {calStatus?.connected ? "Reconnect Google Calendar" : "Connect Google Calendar"}
                          </Button>
                          {calStatus?.connected && (
                            <Button
                              variant="secondary"
                              size="sm"
                              onClick={() => void disconnectGoogleCalendar()}
                            >
                              Disconnect
                            </Button>
                          )}
                        </div>
                      </div>

                      <div className="rounded-xl border border-ink/10 bg-canvas px-4 py-4">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex min-w-0 gap-3">
                            <MessageSquareText className="mt-0.5 size-5 shrink-0 text-sage-700" />
                            <div className="min-w-0">
                              <p className="font-semibold">Messages</p>
                              <p className="mt-1 text-sm text-ink/55">{services.messages.detail}</p>
                              <p className="mt-2 line-clamp-2 text-xs text-ink/45">
                                {services.messages.preview || "No message context synced"}
                              </p>
                            </div>
                          </div>
                          {statusPill(
                            services.messages.ok,
                            services.messages.source === "imessage" ? "Synced" : "Manual",
                          )}
                        </div>
                        <Button
                          variant="secondary"
                          size="sm"
                          className="mt-3"
                          disabled={syncing}
                          onClick={() => void onSyncMessages()}
                        >
                          {syncing ? (
                            <Loader2 className="size-4 animate-spin" />
                          ) : (
                            <MessageSquareText className="size-4" />
                          )}
                          Sync Messages
                        </Button>
                      </div>
                    </div>

                    <div className="rounded-xl bg-ink/5 px-4 py-3 text-sm text-ink/60">
                      MCP actions are ready for the agent bridge. Pending queue:{" "}
                      {services.bridge.pending_actions}.
                    </div>
                    {mcpHelp && (
                      <div className="rounded-xl border border-dusk-200 bg-dusk-50 px-4 py-3 text-sm text-dusk-900">
                        <p className="font-semibold">
                          {mcpHelp === "gmail"
                            ? "Gmail MCP connection"
                            : mcpHelp === "calendar"
                              ? "Calendar MCP connection"
                              : "Messages MCP connection"}
                        </p>
                        <p className="mt-1">
                          Open Cursor Settings, go to MCP, connect{" "}
                          {mcpHelp === "gmail"
                            ? "Gmail"
                            : mcpHelp === "calendar"
                              ? "Google Calendar"
                              : "Messages"}{" "}
                          with OAuth, then return here and re-run preflight.
                        </p>
                        {mcpHelp === "calendar" && (
                          <p className="mt-2 text-dusk-700">
                            Pick write access so tentative callback events can be created.
                          </p>
                        )}
                      </div>
                    )}
                    {bridgeSpecs && (
                      <div className="rounded-xl border border-ink/10 bg-canvas px-4 py-3">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-sm font-semibold">
                            {bridgeSpecs.count} {bridgeSpecs.kind} MCP request
                            {bridgeSpecs.count === 1 ? "" : "s"}
                          </p>
                          <Button variant="secondary" size="sm" onClick={() => void copyBridgeSpecs()}>
                            Copy specs
                          </Button>
                        </div>
                        <pre className="mt-3 max-h-56 overflow-auto rounded-lg bg-ink/5 p-3 text-xs text-ink/70">
                          {JSON.stringify(bridgeSpecs.specs, null, 2)}
                        </pre>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="flex items-center gap-2 text-sm text-ink/50">
                    <Loader2 className="size-4 animate-spin" /> Running preflight…
                  </div>
                )}
                {health && (
                  <div className="rounded-xl border border-ink/10 bg-parchment/60 px-4 py-3 text-xs text-ink/50">
                    Voice stack:{" "}
                    {Object.entries(health.checks)
                      .map(([key, { ok }]) => `${key.replace(/_/g, " ")} ${ok ? "ready" : "missing"}`)
                      .join(" · ")}
                  </div>
                )}
                <Button variant="secondary" size="sm" onClick={() => void refreshHealth()}>
                  <RefreshCw className="size-4" /> Re-run preflight
                </Button>
                <div className="flex gap-3 pt-2">
                  <Button variant="secondary" onClick={() => setStep(3)}>
                    Back
                  </Button>
                  <Button className="flex-1" onClick={() => setStep(5)}>
                    Continue
                  </Button>
                </div>
              </CardContent>
            </>
          )}

          {step === 5 && (
            <>
              <CardHeader>
                <CardTitle>You&apos;re ready</CardTitle>
                <CardDescription>Talk to your agent in the browser or call your Twilio number.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <ul className="space-y-2 text-sm">
                  <li className="flex items-center gap-2">
                    <Check className="size-4 text-sage-600" /> Profile: {config.display_name}
                  </li>
                  <li className="flex items-center gap-2">
                    <Check className="size-4 text-sage-600" /> {uploadSummary ?? "Contacts skipped"}
                  </li>
                  <li className="flex items-center gap-2">
                    <Check className="size-4 text-sage-600" />
                    {config.manual_availability || "Calendar: configure later"}
                  </li>
                  <li className="flex items-center gap-2">
                    <Check className="size-4 text-sage-600" />
                    {config.message_context_summary
                      ? `Message context: ${config.message_context_summary.slice(0, 60)}${config.message_context_summary.length > 60 ? "…" : ""}`
                      : "Message context: none"}
                  </li>
                </ul>
                <p className="rounded-xl bg-canvas px-4 py-3 text-sm text-ink/60">
                  Demo script: call as a known contact, say it&apos;s urgent, leave a callback time, hang up —
                  then check email or <code>server/outbox/</code>.
                </p>
                <Button className="w-full" size="lg" disabled={saving} onClick={() => void finishSetup()}>
                  Try your agent
                </Button>
              </CardContent>
            </>
          )}
        </Card>
      </div>
    </div>
  )
}
