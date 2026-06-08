import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import {
  ArrowLeft,
  ArrowRight,
  CalendarDays,
  Check,
  CheckCircle2,
  CircleDot,
  LockKeyhole,
  Mail,
  Mic,
  PhoneCall,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Square,
  WandSparkles,
} from "lucide-react"
import { SiteHeader } from "@/components/site-header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import {
  activateTenantAgent,
  confirmVoicemailForwarding,
  confirmTenantVoicemailForwarding,
  createTenantSession,
  createTenantVoiceClone,
  createVoiceClone,
  deleteTenantVoiceClone,
  disconnectTenantGoogle,
  fetchOnboardingStatus,
  fetchTenantGoogleStatus,
  fetchTenantSession,
  getTenantGoogleAuthUrl,
  previewDictionProfile,
  saveDictionProfile,
  saveOwnerConfig,
  saveTenantDictionProfile,
  saveTenantProfile,
  tenantOnboardingConfigured,
  type DictionProfile,
  type OnboardingStatus,
  type TenantAgent,
  type TenantGoogleService,
} from "@/lib/api"

const STEPS = ["Profile", "Voicemail", "Voice", "Diction", "Context", "Ready"]
const DRAFT_KEY = "gotchu-onboarding-draft"
const MIN_VOICE_SAMPLE_SECONDS = 20
const MAX_VOICE_SAMPLE_SECONDS = 90
const RECORDING_SCRIPT =
  "Hi, this is my real voice. Thanks for calling. I am tied up right now, but leave me a message and I will get back to you. Sometimes I speak quickly when I am excited, and sometimes I slow down to make a point. Talk soon."
const DICTION_PROMPTS = [
  "A friend asks if you are free later. Reply the way you normally would.",
  "Someone leaves a time-sensitive work message. Say how you would respond.",
  "You are wrapping up a casual call. Write your usual sign-off.",
]
const DICTION_PHRASES = ["gotchu", "sounds good", "talk soon", "I’ll circle back", "appreciate it"]

type Draft = {
  displayName: string
  ownerPhone: string
  ownerEmail: string
  forwardingConfirmed: boolean
  voiceSampleReady: boolean
  dictionSamples: string[]
  dictionPhrases: string[]
  dictionProfile: DictionProfile | null
  tenantId: string
  onboardingToken: string
  tenantAgentPhone: string
  tenantActivationStatus: string
  gmailConnectedEmail: string
  calendarConnectedEmail: string
}

const EMPTY_DRAFT: Draft = {
  displayName: "",
  ownerPhone: "",
  ownerEmail: "",
  forwardingConfirmed: false,
  voiceSampleReady: false,
  dictionSamples: ["", "", ""],
  dictionPhrases: [],
  dictionProfile: null,
  tenantId: "",
  onboardingToken: "",
  tenantAgentPhone: "",
  tenantActivationStatus: "",
  gmailConnectedEmail: "",
  calendarConnectedEmail: "",
}

function loadDraft(): Draft {
  try {
    const saved = JSON.parse(localStorage.getItem(DRAFT_KEY) ?? "{}") as Partial<Draft>
    return {
      ...EMPTY_DRAFT,
      ...saved,
      dictionSamples: Array.isArray(saved.dictionSamples) ? saved.dictionSamples : ["", "", ""],
      dictionPhrases: Array.isArray(saved.dictionPhrases) ? saved.dictionPhrases : [],
      dictionProfile: saved.dictionProfile ?? null,
    }
  } catch {
    return EMPTY_DRAFT
  }
}

function mergeTenantAgent(current: Draft, agent: TenantAgent): Draft {
  return {
    ...current,
    tenantId: agent.tenant_id,
    tenantAgentPhone: agent.agent_phone || agent.forwarding_phone || current.tenantAgentPhone,
    tenantActivationStatus: agent.activation_status,
    gmailConnectedEmail: agent.gmail_connected_email ?? "",
    calendarConnectedEmail: agent.calendar_connected_email ?? "",
    forwardingConfirmed:
      agent.voicemail_forwarding_confirmed || current.forwardingConfirmed,
    voiceSampleReady: agent.voice_clone_status === "ready" || current.voiceSampleReady,
    dictionProfile: agent.diction_profile_lines.length
      ? {
          ready: true,
          phrases: agent.diction_profile_phrases,
          style_notes: agent.diction_profile_style_notes,
          lines: agent.diction_profile_lines,
        }
      : current.dictionProfile,
  }
}

function previewDictionLocally(samples: string[], phrases: string[]): DictionProfile {
  const replies = samples.map((sample) => sample.trim()).filter(Boolean)
  const notes: string[] = []
  const contractionCount = replies.filter((reply) =>
    /\b(?:can't|don't|gonna|gotta|i'll|i'm|lemme|that's|wanna|we'll|we're|you're)\b/i.test(reply),
  ).length
  if (contractionCount >= 2) notes.push("Uses casual contractions and relaxed spoken phrasing.")
  const averageWords =
    replies.reduce((total, reply) => total + reply.split(/\s+/).filter(Boolean).length, 0) /
    Math.max(replies.length, 1)
  if (replies.length >= 3 && averageWords <= 14) notes.push("Prefers short, direct replies.")
  const cleanPhrases = phrases
    .map((phrase) => phrase.trim())
    .filter((phrase) => phrase && phrase.split(/\s+/).length <= 3)
    .slice(0, 6)
  return {
    ready: replies.length >= 3 && (cleanPhrases.length > 0 || notes.length > 0),
    sample_count: replies.length,
    phrases: cleanPhrases,
    style_notes: notes,
    lines: [],
  }
}

export function OnboardingPage() {
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState<Draft>(loadDraft)
  const [status, setStatus] = useState<OnboardingStatus | null>(null)
  const [apiConnected, setApiConnected] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null)
  const [recordingUrl, setRecordingUrl] = useState<string | null>(null)
  const [voiceSampleSeconds, setVoiceSampleSeconds] = useState(0)
  const [voiceConsent, setVoiceConsent] = useState(false)
  const [connectingGoogle, setConnectingGoogle] = useState<TenantGoogleService | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const recordingStartedAtRef = useRef<number | null>(null)
  const recordingTimerRef = useRef<number | null>(null)

  const privateMode = Boolean(apiConnected && status?.private_activation_enabled)
  const tenantMode = tenantOnboardingConfigured() && !privateMode
  const agentPhone =
    draft.tenantAgentPhone ||
    status?.agent_phone ||
    import.meta.env.VITE_AGENT_PHONE_NUMBER ||
    "assigned during activation"

  useEffect(() => {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
  }, [draft])

  useEffect(() => {
    fetchOnboardingStatus()
      .then((nextStatus) => {
        setStatus(nextStatus)
        setApiConnected(true)
        if (!nextStatus.private_activation_enabled) return
        setDraft((current) => ({
          ...current,
          forwardingConfirmed:
            nextStatus.voicemail_forwarding_confirmed || current.forwardingConfirmed,
          voiceSampleReady: nextStatus.voice_clone_ready || current.voiceSampleReady,
          dictionProfile: nextStatus.diction_profile.ready
            ? nextStatus.diction_profile
            : current.dictionProfile,
        }))
      })
      .catch(() => setApiConnected(false))
  }, [])

  useEffect(() => {
    if (!tenantMode || !draft.tenantId || !draft.onboardingToken) return
    fetchTenantSession(draft.tenantId, draft.onboardingToken)
      .then(({ agent }) => {
        setApiConnected(true)
        setDraft((current) => mergeTenantAgent(current, agent))
      })
      .catch(() => setError("Could not resume your cloud activation. Start a new setup draft."))
  }, [draft.onboardingToken, draft.tenantId, tenantMode])

  useEffect(() => {
    return () => {
      if (recordingUrl) URL.revokeObjectURL(recordingUrl)
      if (recordingTimerRef.current) window.clearInterval(recordingTimerRef.current)
      streamRef.current?.getTracks().forEach((track) => track.stop())
    }
  }, [recordingUrl])

  const progress = ((step + 1) / STEPS.length) * 100
  const dictionSampleCount = draft.dictionSamples.filter((sample) => sample.trim()).length
  const voiceSampleTooShort = Boolean(recordedBlob) && voiceSampleSeconds < MIN_VOICE_SAMPLE_SECONDS
  const voiceSampleTooLong = voiceSampleSeconds > MAX_VOICE_SAMPLE_SECONDS
  const voiceSampleDurationLabel = voiceSampleSeconds
    ? `${voiceSampleSeconds}s recorded`
    : `Aim for ${MIN_VOICE_SAMPLE_SECONDS}–60s`

  const next = (nextStep: number) => {
    setError(null)
    setNotice(null)
    setStep(nextStep)
  }

  const saveProfile = async () => {
    if (!draft.displayName || !draft.ownerPhone || !draft.ownerEmail) return
    setBusy(true)
    setError(null)
    try {
      const profile = {
        display_name: draft.displayName,
        owner_phone: draft.ownerPhone,
        owner_email: draft.ownerEmail,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }
      if (privateMode) {
        await saveOwnerConfig(profile)
      } else if (tenantMode) {
        if (draft.tenantId && draft.onboardingToken) {
          try {
            const { agent } = await saveTenantProfile(
              draft.tenantId,
              draft.onboardingToken,
              profile,
            )
            setDraft((current) => mergeTenantAgent(current, agent))
          } catch {
            const { agent, onboarding_token } = await createTenantSession(profile)
            setDraft((current) => ({
              ...mergeTenantAgent(current, agent),
              onboardingToken: onboarding_token,
            }))
            setNotice("Started a fresh cloud activation for this setup.")
          }
        } else {
          const { agent, onboarding_token } = await createTenantSession(profile)
          setDraft((current) => ({
            ...mergeTenantAgent(current, agent),
            onboardingToken: onboarding_token,
          }))
        }
      }
      next(1)
    } catch {
      setError("Could not save your profile. Please try again.")
    } finally {
      setBusy(false)
    }
  }

  const confirmForwarding = async () => {
    if (tenantMode && !draft.tenantAgentPhone) {
      setError("Activate your agent first so Gotchu can assign your forwarding number.")
      return
    }
    setBusy(true)
    setError(null)
    try {
      if (privateMode) await confirmVoicemailForwarding(true)
      if (tenantMode && draft.tenantId && draft.onboardingToken) {
        const { agent } = await confirmTenantVoicemailForwarding(
          draft.tenantId,
          draft.onboardingToken,
          true,
        )
        setDraft((current) => mergeTenantAgent(current, agent))
      } else {
        setDraft({ ...draft, forwardingConfirmed: true })
      }
      setNotice(
        privateMode || tenantMode
          ? "Forwarding confirmation saved. Your agent can now answer missed calls."
          : "Forwarding choice saved in this browser draft. A private activation assigns your live number.",
      )
    } catch {
      setError("Could not save the forwarding confirmation. Please try again.")
    } finally {
      setBusy(false)
    }
  }

  const activateTenant = async () => {
    if (!tenantMode || !draft.tenantId || !draft.onboardingToken) {
      setError("Save your profile first, then activate your cloud agent.")
      return
    }
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const { agent, phone_assignment } = await activateTenantAgent(
        draft.tenantId,
        draft.onboardingToken,
      )
      setDraft((current) => mergeTenantAgent(current, agent))
      if (phone_assignment.assigned) {
        setNotice(
          agent.activation_status === "ready"
            ? "Your agent is active."
            : "Your forwarding number is assigned. Enable conditional forwarding and confirm it before the agent is ready.",
        )
      } else {
        setError(
          phone_assignment.detail ||
            "Your profile is saved, but Gotchu could not assign a forwarding number yet.",
        )
      }
    } catch {
      setError("Could not activate your agent yet. Make sure voice and diction are complete.")
    } finally {
      setBusy(false)
    }
  }

  const stopRecordingTimer = () => {
    if (recordingTimerRef.current) {
      window.clearInterval(recordingTimerRef.current)
      recordingTimerRef.current = null
    }
  }

  const measuredVoiceSeconds = () => {
    if (recordingStartedAtRef.current) {
      return Math.round((Date.now() - recordingStartedAtRef.current) / 1000)
    }
    return Math.round(voiceSampleSeconds)
  }

  const startRecording = async () => {
    setError(null)
    setNotice(null)
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("This browser cannot record audio. Try the latest Chrome or Safari.")
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream)
      chunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const seconds = measuredVoiceSeconds()
        stopRecordingTimer()
        recordingStartedAtRef.current = null
        recorderRef.current = null
        streamRef.current = null
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" })
        if (recordingUrl) URL.revokeObjectURL(recordingUrl)
        setRecordedBlob(blob)
        setRecordingUrl(URL.createObjectURL(blob))
        setVoiceSampleSeconds(seconds)
        setDraft((current) => ({ ...current, voiceSampleReady: false }))
        stream.getTracks().forEach((track) => track.stop())
      }
      recorder.start()
      recorderRef.current = recorder
      streamRef.current = stream
      recordingStartedAtRef.current = Date.now()
      setVoiceSampleSeconds(0)
      recordingTimerRef.current = window.setInterval(() => {
        const seconds = measuredVoiceSeconds()
        setVoiceSampleSeconds(seconds)
        if (seconds >= MAX_VOICE_SAMPLE_SECONDS && recorder.state === "recording") {
          recorder.stop()
          setRecording(false)
        }
      }, 250)
      setRecording(true)
    } catch {
      setError("Microphone access is needed to record your voice sample.")
    }
  }

  const stopRecording = () => {
    recorderRef.current?.stop()
    recorderRef.current = null
    streamRef.current = null
    stopRecordingTimer()
    setRecording(false)
  }

  const clearLocalVoiceSample = () => {
    if (recordingUrl) URL.revokeObjectURL(recordingUrl)
    setRecordedBlob(null)
    setRecordingUrl(null)
    setVoiceSampleSeconds(0)
    setVoiceConsent(false)
    setDraft((current) => ({ ...current, voiceSampleReady: false }))
  }

  const resetVoice = async () => {
    setError(null)
    setNotice(null)
    if (tenantMode && draft.tenantId && draft.onboardingToken && draft.voiceSampleReady) {
      setBusy(true)
      try {
        const { agent } = await deleteTenantVoiceClone(draft.tenantId, draft.onboardingToken)
        setDraft((current) => mergeTenantAgent({ ...current, voiceSampleReady: false }, agent))
        clearLocalVoiceSample()
        setNotice("Saved voice removed. Record a fresh sample when you are ready.")
      } catch {
        setError("Could not remove the saved voice clone. Please try again.")
      } finally {
        setBusy(false)
      }
      return
    }
    clearLocalVoiceSample()
  }

  const saveVoice = async () => {
    if (!recordedBlob || !voiceConsent) return
    if (voiceSampleSeconds < MIN_VOICE_SAMPLE_SECONDS) {
      setError(`Record at least ${MIN_VOICE_SAMPLE_SECONDS} seconds before preparing your voice.`)
      return
    }
    if (voiceSampleSeconds > MAX_VOICE_SAMPLE_SECONDS) {
      setError(`Keep the recording under ${MAX_VOICE_SAMPLE_SECONDS} seconds and try again.`)
      return
    }
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      if (privateMode) {
        const file = new File([recordedBlob], "owner-voice.webm", {
          type: recordedBlob.type || "audio/webm",
        })
        await createVoiceClone(file, `${draft.displayName || "Owner"} voice`, voiceSampleSeconds)
      } else if (tenantMode && draft.tenantId && draft.onboardingToken) {
        const file = new File([recordedBlob], "owner-voice.webm", {
          type: recordedBlob.type || "audio/webm",
        })
        const { agent } = await createTenantVoiceClone(
          draft.tenantId,
          draft.onboardingToken,
          file,
          `${draft.displayName || "Owner"} voice`,
          voiceSampleSeconds,
        )
        setDraft((current) => mergeTenantAgent(current, agent))
      }
      setDraft((current) => ({ ...current, voiceSampleReady: true }))
      setNotice(
        privateMode || tenantMode
          ? "Your consented voice clone is ready for future calls."
          : "Voice sample is ready in this tab. It has not been uploaded from this public preview.",
      )
    } catch {
      setError("Your voice sample could not be prepared. Record a clear 20–60 second sample and retry.")
    } finally {
      setBusy(false)
    }
  }

  const previewDiction = async () => {
    setBusy(true)
    setError(null)
    try {
      const profile = privateMode
        ? await previewDictionProfile(draft.dictionSamples, draft.dictionPhrases)
        : tenantMode
          ? await previewDictionProfile(draft.dictionSamples, draft.dictionPhrases)
          : previewDictionLocally(draft.dictionSamples, draft.dictionPhrases)
      if (!profile.ready) {
        setError("Add all three replies and choose a phrase or use a little more of your natural style.")
        return
      }
      setDraft({ ...draft, dictionProfile: profile })
      setNotice("Profile ready. Review what Gotchu learned, then finish setup.")
    } catch {
      setError("Could not derive a diction profile. Add three short, natural replies and retry.")
    } finally {
      setBusy(false)
    }
  }

  const saveDiction = async () => {
    if (!draft.dictionProfile?.ready) return
    // Resume case: the profile is already persisted server-side and the raw
    // replies were intentionally discarded (never restored to dictionSamples).
    // Re-deriving from fewer than three samples would 422 and trap the user on
    // this step, so just advance when there are no fresh replies to save.
    if (dictionSampleCount < 3) {
      next(4)
      return
    }
    setBusy(true)
    setError(null)
    try {
      if (privateMode) await saveDictionProfile(draft.dictionSamples, draft.dictionPhrases)
      if (tenantMode && draft.tenantId && draft.onboardingToken) {
        const { agent } = await saveTenantDictionProfile(
          draft.tenantId,
          draft.onboardingToken,
          draft.dictionSamples,
          draft.dictionPhrases,
        )
        setDraft((current) => mergeTenantAgent(current, agent))
      }
      next(4)
    } catch {
      setError("Could not save your diction profile. Please try again.")
    } finally {
      setBusy(false)
    }
  }

  const connectTenantGoogle = async (service: TenantGoogleService) => {
    if (!tenantMode || !draft.tenantId || !draft.onboardingToken) {
      setError("Save your profile first, then connect Google.")
      return
    }
    setConnectingGoogle(service)
    setError(null)
    setNotice(null)
    try {
      const { url } = await getTenantGoogleAuthUrl(
        draft.tenantId,
        draft.onboardingToken,
        service,
      )
      const popup = window.open(url, `tenant-${service}-oauth`, "width=520,height=680")
      await new Promise<void>((resolve) => {
        let elapsed = 0
        const id = window.setInterval(async () => {
          elapsed += 1500
          try {
            const { status: googleStatus, agent } = await fetchTenantGoogleStatus(
              draft.tenantId,
              draft.onboardingToken,
              service,
            )
            setDraft((current) => mergeTenantAgent(current, agent))
            if (googleStatus.connected) {
              window.clearInterval(id)
              resolve()
              return
            }
          } catch {
            /* keep polling while the OAuth popup is active */
          }
          if ((popup && popup.closed) || elapsed > 180000) {
            window.clearInterval(id)
            resolve()
          }
        }, 1500)
      })
      const { status: googleStatus, agent } = await fetchTenantGoogleStatus(
        draft.tenantId,
        draft.onboardingToken,
        service,
      )
      setDraft((current) => mergeTenantAgent(current, agent))
      setNotice(
        googleStatus.connected
          ? `${service === "gmail" ? "Gmail" : "Calendar"} connected.`
          : `Google ${service} connection was not completed.`,
      )
    } catch {
      setError(
        service === "gmail"
          ? "Could not start the Gmail connection."
          : "Could not start the Google Calendar connection.",
      )
    } finally {
      setConnectingGoogle(null)
    }
  }

  const disconnectGoogle = async (service: TenantGoogleService) => {
    if (!draft.tenantId || !draft.onboardingToken) return
    setConnectingGoogle(service)
    setError(null)
    setNotice(null)
    try {
      const { agent } = await disconnectTenantGoogle(
        draft.tenantId,
        draft.onboardingToken,
        service,
      )
      setDraft((current) => mergeTenantAgent(current, agent))
      setNotice(`${service === "gmail" ? "Gmail" : "Calendar"} disconnected.`)
    } catch {
      setError("Could not disconnect Google. Please try again.")
    } finally {
      setConnectingGoogle(null)
    }
  }

  return (
    <div className="min-h-screen bg-wash">
      <SiteHeader />
      <main className="mx-auto max-w-3xl px-6 py-10">
        <Link to="/" className="inline-flex items-center gap-1 text-sm text-ink/55 hover:text-ink">
          <ArrowLeft className="size-4" /> Back home
        </Link>

        <div className="mt-7 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-sage-700">
              Personal setup
            </p>
            <h1 className="mt-2 text-3xl font-semibold sm:text-4xl">Make Gotchu sound like you.</h1>
            <p className="mt-2 max-w-xl text-sm leading-6 text-ink/60">
              Route missed calls, prepare a consented voice, and teach the assistant the phrases
              that make your messages feel familiar.
            </p>
          </div>
          <span className="w-fit rounded-full border border-ink/10 bg-parchment/70 px-3 py-1.5 text-xs font-semibold text-ink/60">
            {privateMode ? "Private activation" : tenantMode ? "Cloud activation" : "Public preview"}
          </span>
        </div>

        <div className="mt-7">
          <div className="mb-2 flex items-center justify-between text-xs font-semibold text-ink/55">
            <span>Step {step + 1} of {STEPS.length}</span>
            <span>{STEPS[step]}</span>
          </div>
          <Progress value={progress} />
        </div>

        {!privateMode && !tenantMode && apiConnected !== null && (
          <div className="mt-5 flex gap-3 rounded-2xl border border-dusk-200 bg-dusk-50/80 px-4 py-3 text-sm text-dusk-900">
            <LockKeyhole className="mt-0.5 size-4 shrink-0" />
            <p>
              This public page keeps your setup as a browser-only draft. Voice audio is not
              uploaded and your live forwarding number is assigned only during private activation.
            </p>
          </div>
        )}

        {error && (
          <div className="mt-5 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}
        {notice && (
          <div className="mt-5 rounded-2xl border border-sage-200 bg-sage-50 px-4 py-3 text-sm text-sage-800">
            {notice}
          </div>
        )}

        <Card className="mt-6 overflow-hidden bg-parchment/90">
          {step === 0 && (
            <>
              <CardHeader>
                <CardTitle>Start with the basics</CardTitle>
                <CardDescription>
                  These details personalize the greeting and tell Gotchu where to send your
                  voicemail summaries.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <Label htmlFor="onboarding-name">Your name</Label>
                  <Input
                    id="onboarding-name"
                    placeholder="Alex"
                    value={draft.displayName}
                    onChange={(event) => setDraft({ ...draft, displayName: event.target.value })}
                  />
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <Label htmlFor="onboarding-phone">Mobile number</Label>
                    <Input
                      id="onboarding-phone"
                      type="tel"
                      placeholder="+1 415 555 1234"
                      value={draft.ownerPhone}
                      onChange={(event) => setDraft({ ...draft, ownerPhone: event.target.value })}
                    />
                  </div>
                  <div>
                    <Label htmlFor="onboarding-email">Notification email</Label>
                    <Input
                      id="onboarding-email"
                      type="email"
                      placeholder="you@example.com"
                      value={draft.ownerEmail}
                      onChange={(event) => setDraft({ ...draft, ownerEmail: event.target.value })}
                    />
                  </div>
                </div>
                <Button
                  className="w-full"
                  disabled={!draft.displayName || !draft.ownerPhone || !draft.ownerEmail || busy}
                  onClick={() => void saveProfile()}
                >
                  Continue to voicemail <ArrowRight className="size-4" />
                </Button>
              </CardContent>
            </>
          )}

          {step === 1 && (
            <>
              <CardHeader>
                <CardTitle>Connect missed calls</CardTitle>
                <CardDescription>
                  Keep your existing number. Conditional forwarding sends only unanswered calls to
                  Gotchu, so calls still ring your phone first.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="rounded-2xl border border-sage-200 bg-sage-50/80 p-4">
                  <p className="text-xs font-semibold uppercase tracking-[0.14em] text-sage-700">
                    Your forwarding destination
                  </p>
                  <p className="mt-2 font-display text-2xl font-semibold text-sage-900">{agentPhone}</p>
                </div>
                {tenantMode && !draft.tenantAgentPhone ? (
                  <div className="rounded-xl border border-ink/10 bg-canvas/60 px-4 py-3 text-sm leading-6 text-ink/60">
                    Gotchu will assign your dedicated forwarding number after your voice and
                    diction profile are saved. You will enable conditional forwarding at the
                    final activation step.
                  </div>
                ) : (
                  <>
                    <ol className="space-y-3 text-sm text-ink/70">
                      <li className="flex gap-3">
                        <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-dusk-100 text-xs font-bold text-dusk-800">1</span>
                        Open your carrier’s call-forwarding settings.
                      </li>
                      <li className="flex gap-3">
                        <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-dusk-100 text-xs font-bold text-dusk-800">2</span>
                        Choose conditional forwarding for unanswered calls and paste the destination above.
                      </li>
                      <li className="flex gap-3">
                        <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-dusk-100 text-xs font-bold text-dusk-800">3</span>
                        Call yourself once and let it ring through to confirm the handoff.
                      </li>
                    </ol>
                    <Button variant="secondary" className="w-full" disabled={busy} onClick={() => void confirmForwarding()}>
                      {draft.forwardingConfirmed ? <Check className="size-4" /> : <PhoneCall className="size-4" />}
                      {draft.forwardingConfirmed ? "Forwarding confirmed" : "I enabled conditional forwarding"}
                    </Button>
                  </>
                )}
                <div className="flex gap-3">
                  <Button variant="secondary" onClick={() => next(0)}>Back</Button>
                  <Button
                    className="flex-1"
                    disabled={!tenantMode && !draft.forwardingConfirmed}
                    onClick={() => next(2)}
                  >
                    Continue to voice <ArrowRight className="size-4" />
                  </Button>
                </div>
              </CardContent>
            </>
          )}

          {step === 2 && (
            <>
              <CardHeader>
                <CardTitle>Prepare your voice</CardTitle>
                <CardDescription>
                  Read this short passage naturally. During activation, the sample creates an
                  owner-only Gradium voice clone used for your agent’s replies.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <blockquote className="rounded-2xl border border-ink/10 bg-canvas/70 px-4 py-4 text-sm leading-6 text-ink/70">
                  “{RECORDING_SCRIPT}”
                </blockquote>
                <div className="flex flex-wrap gap-3">
                  {!recording ? (
                    <Button variant="secondary" onClick={() => void startRecording()}>
                      <Mic className="size-4" /> Record my sample
                    </Button>
                  ) : (
                    <Button variant="secondary" onClick={stopRecording}>
                      <Square className="size-4 fill-current" /> Stop recording
                    </Button>
                  )}
                  {recording && (
                    <span className="inline-flex items-center gap-2 text-sm font-semibold text-red-700">
                      <CircleDot className="size-4 animate-pulse fill-current" /> Recording{" "}
                      {voiceSampleSeconds}s
                    </span>
                  )}
                  {recordingUrl && (
                    <>
                      <audio controls src={recordingUrl} className="h-11 max-w-full" />
                      <Button variant="ghost" disabled={busy} onClick={() => void resetVoice()}>
                        <RotateCcw className="size-4" /> Record again
                      </Button>
                    </>
                  )}
                  {tenantMode && draft.voiceSampleReady && !recordingUrl && (
                    <Button variant="ghost" disabled={busy} onClick={() => void resetVoice()}>
                      <RotateCcw className="size-4" /> Remove saved voice
                    </Button>
                  )}
                </div>
                <div
                  className={`rounded-2xl border px-4 py-3 text-sm ${
                    voiceSampleTooShort || voiceSampleTooLong
                      ? "border-amber-200 bg-amber-50 text-amber-800"
                      : "border-ink/10 bg-canvas/60 text-ink/60"
                  }`}
                >
                  <p className="font-semibold">{voiceSampleDurationLabel}</p>
                  <p className="mt-1">
                    {voiceSampleTooShort
                      ? `Please record at least ${MIN_VOICE_SAMPLE_SECONDS} seconds so Gradium has enough speech to clone.`
                      : voiceSampleTooLong
                        ? `This sample is over ${MAX_VOICE_SAMPLE_SECONDS} seconds. Record a shorter one.`
                        : "A clear 20–60 second sample gives the voice clone enough range without over-sharing."}
                  </p>
                </div>
                <label className="flex items-start gap-3 rounded-2xl border border-ink/10 bg-canvas/60 px-4 py-3 text-sm text-ink/70">
                  <input
                    type="checkbox"
                    className="mt-1 size-4 accent-sage-700"
                    checked={voiceConsent}
                    onChange={(event) => setVoiceConsent(event.target.checked)}
                  />
                  <span>
                    I consent to creating a voice similar to mine for my personal voicemail agent.
                    I understand it must never be used as a caller’s voice.
                  </span>
                </label>
                <Button
                  variant="secondary"
                  className="w-full"
                  disabled={!recordedBlob || !voiceConsent || voiceSampleTooShort || voiceSampleTooLong || busy}
                  onClick={() => void saveVoice()}
                >
                  <WandSparkles className="size-4" />
                  {draft.voiceSampleReady ? "Voice sample ready" : "Prepare my voice"}
                </Button>
                <div className="flex gap-3">
                  <Button variant="secondary" onClick={() => next(1)}>Back</Button>
                  <Button className="flex-1" disabled={!draft.voiceSampleReady} onClick={() => next(3)}>
                    Continue to diction <ArrowRight className="size-4" />
                  </Button>
                </div>
              </CardContent>
            </>
          )}

          {step === 3 && (
            <>
              <CardHeader>
                <CardTitle>Teach your diction</CardTitle>
                <CardDescription>
                  Write three replies the way you actually talk. Gotchu keeps a small, filtered
                  style profile: recurring phrases and rhythm notes, not your raw examples.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="space-y-4">
                  {DICTION_PROMPTS.map((prompt, index) => (
                    <div key={prompt}>
                      <Label htmlFor={`diction-sample-${index}`}>Reply {index + 1}</Label>
                      <p className="mt-1 text-xs leading-5 text-ink/50">{prompt}</p>
                      <textarea
                        id={`diction-sample-${index}`}
                        rows={3}
                        maxLength={600}
                        placeholder="Write it like a text you would actually send."
                        className="mt-2 flex w-full rounded-xl border border-ink/15 bg-parchment px-4 py-3 text-sm shadow-sm placeholder:text-ink/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sage-500"
                        value={draft.dictionSamples[index] ?? ""}
                        onChange={(event) => {
                          const dictionSamples = [...draft.dictionSamples]
                          dictionSamples[index] = event.target.value
                          setDraft({ ...draft, dictionSamples, dictionProfile: null })
                        }}
                      />
                    </div>
                  ))}
                </div>

                <div>
                  <p className="text-sm font-semibold">Phrases you reach for often</p>
                  <p className="mt-1 text-xs leading-5 text-ink/50">
                    Optional hints. Pick only phrases that genuinely sound like you.
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {DICTION_PHRASES.map((phrase) => {
                      const selected = draft.dictionPhrases.includes(phrase)
                      return (
                    <button
                      key={phrase}
                      type="button"
                      className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                        selected
                          ? "border-sage-500 bg-sage-100 text-sage-900"
                          : "border-ink/10 bg-canvas text-ink/60 hover:border-sage-400 hover:text-sage-800"
                      }`}
                      onClick={() =>
                        setDraft({
                          ...draft,
                          dictionPhrases: selected
                            ? draft.dictionPhrases.filter((item) => item !== phrase)
                            : [...draft.dictionPhrases, phrase],
                          dictionProfile: null,
                        })
                      }
                    >
                      {selected ? <Check className="mr-1 inline size-3" /> : "+ "}
                      {phrase}
                    </button>
                      )
                    })}
                  </div>
                </div>

                <Button
                  variant="secondary"
                  className="w-full"
                  disabled={dictionSampleCount < 3 || busy}
                  onClick={() => void previewDiction()}
                >
                  <Sparkles className="size-4" />
                  {draft.dictionProfile?.ready ? "Refresh my profile" : "Preview what Gotchu learned"}
                </Button>

                {draft.dictionProfile?.ready && (
                  <div className="rounded-2xl border border-sage-200 bg-sage-50/80 px-4 py-4 text-sm text-sage-900">
                    <p className="font-semibold">Your filtered diction profile</p>
                    {draft.dictionProfile.phrases.length > 0 && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {draft.dictionProfile.phrases.map((phrase) => (
                          <span key={phrase} className="rounded-full bg-parchment px-3 py-1 text-xs font-semibold">
                            “{phrase}”
                          </span>
                        ))}
                      </div>
                    )}
                    {draft.dictionProfile.style_notes.length > 0 && (
                      <ul className="mt-3 space-y-1 text-xs leading-5 text-sage-800">
                        {draft.dictionProfile.style_notes.map((note) => (
                          <li key={note}>• {note}</li>
                        ))}
                      </ul>
                    )}
                    <p className="mt-3 text-xs text-sage-700">
                      Raw replies are discarded after this profile is derived.
                    </p>
                  </div>
                )}

                <div className="rounded-xl border border-ink/10 bg-canvas/60 px-4 py-3 text-xs leading-5 text-ink/55">
                  {dictionSampleCount}/3 replies added. Sensitive values such as emails, phone
                  numbers, links, and tokens are filtered before a profile is stored.
                </div>
                <div className="flex gap-3">
                  <Button variant="secondary" onClick={() => next(2)}>Back</Button>
                  <Button
                    className="flex-1"
                    disabled={!draft.dictionProfile?.ready || busy}
                    onClick={() => void saveDiction()}
                  >
                    Finish setup <ArrowRight className="size-4" />
                  </Button>
                </div>
              </CardContent>
            </>
          )}

          {step === 4 && (
            <>
              <CardHeader>
                <CardTitle>Connect your context</CardTitle>
                <CardDescription>
                  Optional, but this is what makes the live agent more useful: Gmail sends owner
                  notifications from your account, and Calendar can check conflicts before booking
                  callbacks.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="rounded-2xl border border-ink/10 bg-canvas/70 p-4">
                    <Mail className="size-5 text-sage-700" />
                    <p className="mt-3 font-semibold">Gmail</p>
                    <p className="mt-1 min-h-10 text-sm leading-5 text-ink/55">
                      {draft.gmailConnectedEmail
                        ? `Connected as ${draft.gmailConnectedEmail}.`
                        : "Send voicemail summaries through your own connected Gmail."}
                    </p>
                    {tenantMode ? (
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button
                          variant="secondary"
                          disabled={connectingGoogle === "gmail"}
                          onClick={() => void connectTenantGoogle("gmail")}
                        >
                          {draft.gmailConnectedEmail ? "Reconnect Gmail" : "Connect Gmail"}
                        </Button>
                        {draft.gmailConnectedEmail && (
                          <Button
                            variant="ghost"
                            disabled={connectingGoogle === "gmail"}
                            onClick={() => void disconnectGoogle("gmail")}
                          >
                            Disconnect
                          </Button>
                        )}
                      </div>
                    ) : (
                      <p className="mt-4 text-xs text-ink/50">
                        Private Gmail setup is managed from the setup console.
                      </p>
                    )}
                  </div>

                  <div className="rounded-2xl border border-ink/10 bg-canvas/70 p-4">
                    <CalendarDays className="size-5 text-sage-700" />
                    <p className="mt-3 font-semibold">Google Calendar</p>
                    <p className="mt-1 min-h-10 text-sm leading-5 text-ink/55">
                      {draft.calendarConnectedEmail
                        ? `Connected as ${draft.calendarConnectedEmail}.`
                        : "Check availability and add tentative callback events."}
                    </p>
                    {tenantMode ? (
                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button
                          variant="secondary"
                          disabled={connectingGoogle === "calendar"}
                          onClick={() => void connectTenantGoogle("calendar")}
                        >
                          {draft.calendarConnectedEmail ? "Reconnect Calendar" : "Connect Calendar"}
                        </Button>
                        {draft.calendarConnectedEmail && (
                          <Button
                            variant="ghost"
                            disabled={connectingGoogle === "calendar"}
                            onClick={() => void disconnectGoogle("calendar")}
                          >
                            Disconnect
                          </Button>
                        )}
                      </div>
                    ) : (
                      <p className="mt-4 text-xs text-ink/50">
                        Private Calendar setup is managed from the setup console.
                      </p>
                    )}
                  </div>
                </div>

                <div className="rounded-xl border border-ink/10 bg-canvas/60 px-4 py-3 text-xs leading-5 text-ink/55">
                  Google tokens are stored in a tenant-scoped vault. Your onboarding profile only
                  keeps safe connection metadata like the connected email address.
                </div>
                <div className="flex gap-3">
                  <Button variant="secondary" onClick={() => next(3)}>Back</Button>
                  <Button className="flex-1" onClick={() => next(5)}>
                    Continue to ready <ArrowRight className="size-4" />
                  </Button>
                </div>
              </CardContent>
            </>
          )}

          {step === 5 && (
            <>
              <CardHeader>
                <CardTitle>
                  {privateMode
                    ? "Your agent is ready."
                    : tenantMode
                      ? draft.tenantAgentPhone
                        ? "Your agent is ready."
                        : "Activate your agent."
                      : "Your setup draft is ready."}
                </CardTitle>
                <CardDescription>
                  {privateMode || (tenantMode && draft.tenantAgentPhone && draft.forwardingConfirmed)
                    ? "Missed calls now have a familiar, context-aware place to land."
                    : tenantMode
                      ? "Your voice and diction profile are saved. Activate to assign a dedicated forwarding number."
                    : "You have prepared everything needed for a private activation without uploading personal audio from the public page."}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="grid gap-3 sm:grid-cols-5">
                  {[
                    ["Voicemail", draft.forwardingConfirmed ? "Connected" : "Pending", PhoneCall],
                    ["Voice", draft.voiceSampleReady ? "Prepared" : "Pending", Sparkles],
                    ["Diction", draft.dictionProfile?.ready ? "Prepared" : "Pending", ShieldCheck],
                    ["Gmail", draft.gmailConnectedEmail ? "Connected" : "Optional", Mail],
                    [
                      "Calendar",
                      draft.calendarConnectedEmail ? "Connected" : "Optional",
                      CalendarDays,
                    ],
                  ].map(([label, value, Icon]) => (
                    <div key={String(label)} className="rounded-2xl border border-ink/10 bg-canvas/70 p-4">
                      <Icon className="size-5 text-sage-700" />
                      <p className="mt-3 text-sm font-semibold">{String(label)}</p>
                      <p className="mt-1 text-xs text-ink/55">{String(value)}</p>
                    </div>
                  ))}
                </div>
                {tenantMode && !draft.tenantAgentPhone && (
                  <div className="rounded-2xl border border-dusk-200 bg-dusk-50/80 px-4 py-4 text-sm leading-6 text-dusk-900">
                    <p className="font-semibold">Ready to assign your forwarding number</p>
                    <p className="mt-1">
                      Activation provisions a tenant-specific Twilio number, points it at your
                      voice agent, and stores it on your cloud profile.
                    </p>
                    <Button className="mt-4 w-full" disabled={busy} onClick={() => void activateTenant()}>
                      <PhoneCall className="size-4" />
                      Activate my agent
                    </Button>
                  </div>
                )}
                {tenantMode && draft.tenantAgentPhone && (
                  <div className="rounded-2xl border border-sage-200 bg-sage-50/80 p-4 text-sage-900">
                    <p className="text-xs font-semibold uppercase tracking-[0.14em] text-sage-700">
                      Your forwarding destination
                    </p>
                    <p className="mt-2 font-display text-2xl font-semibold">{draft.tenantAgentPhone}</p>
                    <ol className="mt-4 space-y-2 text-sm leading-6">
                      <li>1. Open your carrier's conditional call-forwarding settings.</li>
                      <li>2. Forward unanswered calls to the number above.</li>
                      <li>3. Call your own phone once and let it ring through.</li>
                    </ol>
                    <Button
                      variant="secondary"
                      className="mt-4 w-full"
                      disabled={busy}
                      onClick={() => void confirmForwarding()}
                    >
                      {draft.forwardingConfirmed ? <Check className="size-4" /> : <PhoneCall className="size-4" />}
                      {draft.forwardingConfirmed ? "Forwarding confirmed" : "I enabled conditional forwarding"}
                    </Button>
                  </div>
                )}
                <div className="rounded-2xl border border-sage-200 bg-sage-50 px-4 py-4 text-sm text-sage-900">
                  <CheckCircle2 className="mb-2 size-5" />
                  {privateMode
                    ? "Your live owner settings are saved. Open the demo call to hear the agent."
                    : tenantMode
                      ? draft.tenantAgentPhone && draft.forwardingConfirmed
                        ? "Your private cloud agent is ready. Missed calls forwarded to the destination above route into your own voice, diction, and tenant context."
                        : draft.tenantAgentPhone
                          ? "Your private cloud agent is provisioned, but voicemail is not connected until conditional forwarding is enabled and confirmed."
                        : "Your private cloud profile is saved. Activate it to provision the dedicated forwarding number."
                    : "This preview deliberately stops before live activation. A production rollout needs authenticated per-user provisioning so one visitor can never overwrite another person’s agent."}
                </div>
                <div className="flex flex-col gap-3 sm:flex-row">
                  <Button variant="secondary" onClick={() => next(0)}>Review setup</Button>
                  {privateMode && (
                    <Button asChild className="flex-1">
                      <Link to="/try">
                        Try a demo call
                        <Play className="size-4" />
                      </Link>
                    </Button>
                  )}
                </div>
              </CardContent>
            </>
          )}
        </Card>
      </main>
    </div>
  )
}
