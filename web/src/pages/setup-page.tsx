import { useCallback, useEffect, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Loader2,
  Upload,
  XCircle,
} from "lucide-react"
import { SiteHeader } from "@/components/site-header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import {
  fetchHealth,
  fetchOwnerConfig,
  saveOwnerConfig,
  syncMessageContext,
  uploadContacts,
  type HealthCheck,
  type OwnerConfig,
} from "@/lib/api"

const STEPS = ["You", "Contacts", "Context", "Calendar", "Services", "Try"]

export function SetupPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [config, setConfig] = useState<OwnerConfig | null>(null)
  const [health, setHealth] = useState<HealthCheck | null>(null)
  const [uploadSummary, setUploadSummary] = useState<string | null>(null)
  const [sampleNames, setSampleNames] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editingSummary, setEditingSummary] = useState(false)
  const [summaryDraft, setSummaryDraft] = useState("")
  const [syncing, setSyncing] = useState(false)

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
      const h = await fetchHealth()
      setHealth(h)
      setSampleNames(h.contacts.sample_names)
      setUploadSummary(h.contacts.summary)
    } catch {
      setError("Could not reach onboarding API. Start it with: uv run onboarding_api.py")
    }
  }, [])

  useEffect(() => {
    if (step >= 4) void refreshHealth()
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
      setEditingSummary(false)
    } catch {
      setError("Sync failed. Is the onboarding API running?")
    } finally {
      setSyncing(false)
    }
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
                <CardTitle>Recent messages (summary)</CardTitle>
                <CardDescription>
                  A short, redacted summary of recent threads the agent should know about — topics and
                  urgency only. Full chat logs are never stored or sent to the agent.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
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
                <CardTitle>Calendar availability</CardTitle>
                <CardDescription>
                  Google Calendar OAuth coming with P2. For now, describe when you&apos;re free for callbacks.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
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
                <CardTitle>Services checklist</CardTitle>
                <CardDescription>
                  API keys stay in <code className="text-xs">server/.env</code> — we only show status here.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {health ? (
                  Object.entries(health.checks).map(([key, { ok, detail }]) => (
                    <div
                      key={key}
                      className="flex items-start gap-3 rounded-xl border border-ink/10 bg-canvas px-4 py-3 text-sm"
                    >
                      {ok ? (
                        <Check className="mt-0.5 size-4 shrink-0 text-sage-600" />
                      ) : (
                        <XCircle className="mt-0.5 size-4 shrink-0 text-amber-500" />
                      )}
                      <div>
                        <p className="font-medium capitalize">{key.replace(/_/g, " ")}</p>
                        <p className="text-ink/50">{detail}</p>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="flex items-center gap-2 text-sm text-ink/50">
                    <Loader2 className="size-4 animate-spin" /> Running preflight…
                  </div>
                )}
                <Button variant="secondary" size="sm" onClick={() => void refreshHealth()}>
                  Re-run preflight
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
