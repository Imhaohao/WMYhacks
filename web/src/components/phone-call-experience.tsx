import { useCallback, useEffect, useRef, useState } from "react"
import { Mic, MicOff, Phone, PhoneOff, Volume2 } from "lucide-react"

type CallStatus = "idle" | "connecting" | "active" | "ended" | "error"

/* Bar heights for the "agent speaking" visualizer — fixed weights so the bars
   animate smoothly off a single audio level instead of flickering randomly. */
const BAR_WEIGHTS = [0.45, 0.75, 1, 0.8, 0.55, 0.85, 0.5]

function waitForIceGathering(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve()
  return new Promise((resolve) => {
    const done = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", done)
        resolve()
      }
    }
    pc.addEventListener("icegatheringstatechange", done)
    // Safety: don't hang forever if a candidate stalls — send what we have.
    setTimeout(resolve, 2000)
  })
}

function fmt(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${s.toString().padStart(2, "0")}`
}

/**
 * A phone-call UI for the bot, rendered inside the iPhone mockup. Establishes a
 * SmallWebRTC connection to the bot's `/api/offer` endpoint (the same one the
 * Pipecat prebuilt client uses) with a hand-rolled RTCPeerConnection — no SDK.
 */
export function PhoneCallExperience({
  baseUrl,
  agentName,
  phone,
}: {
  baseUrl: string
  agentName: string
  phone: string
}) {
  const [status, setStatus] = useState<CallStatus>("idle")
  const [durationSec, setDurationSec] = useState(0)
  const [muted, setMuted] = useState(false)
  const [botLevel, setBotLevel] = useState(0)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const pcRef = useRef<RTCPeerConnection | null>(null)
  const localStreamRef = useRef<MediaStream | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const rafRef = useRef<number | null>(null)

  const cleanup = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    rafRef.current = null
    localStreamRef.current?.getTracks().forEach((t) => t.stop())
    localStreamRef.current = null
    audioCtxRef.current?.close().catch(() => {})
    audioCtxRef.current = null
    if (pcRef.current) {
      pcRef.current.onconnectionstatechange = null
      pcRef.current.ontrack = null
      pcRef.current.close()
      pcRef.current = null
    }
    if (audioRef.current) audioRef.current.srcObject = null
    setBotLevel(0)
    setMuted(false)
  }, [])

  const endCall = useCallback(
    (next: CallStatus = "ended") => {
      cleanup()
      setStatus(next)
      if (next === "ended") window.setTimeout(() => setStatus("idle"), 1400)
    },
    [cleanup],
  )

  // Tap the remote (bot) audio stream to drive the speaking visualizer.
  const startAnalyser = useCallback((stream: MediaStream) => {
    try {
      const ctx = new AudioContext()
      audioCtxRef.current = ctx
      const source = ctx.createMediaStreamSource(stream)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 256
      analyser.smoothingTimeConstant = 0.75
      source.connect(analyser) // not connected to destination — <audio> plays it
      const data = new Uint8Array(analyser.frequencyBinCount)
      const tick = () => {
        analyser.getByteFrequencyData(data)
        let sum = 0
        for (let i = 0; i < data.length; i++) sum += data[i]
        setBotLevel(Math.min(1, sum / data.length / 140))
        rafRef.current = requestAnimationFrame(tick)
      }
      tick()
    } catch {
      /* analyser is cosmetic — ignore failures */
    }
  }, [])

  const connect = useCallback(async () => {
    setErrorMsg(null)
    setStatus("connecting")
    try {
      const pc = new RTCPeerConnection({
        iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
      })
      pcRef.current = pc

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
      localStreamRef.current = stream
      stream.getTracks().forEach((t) => pc.addTrack(t, stream))

      pc.ontrack = (e) => {
        const [remote] = e.streams
        if (!remote) return
        if (audioRef.current) {
          audioRef.current.srcObject = remote
          audioRef.current.play?.().catch(() => {})
        }
        startAnalyser(remote)
      }

      pc.onconnectionstatechange = () => {
        const s = pc.connectionState
        if (s === "connected") setStatus("active")
        else if (s === "failed" || s === "disconnected" || s === "closed") {
          if (pcRef.current) endCall("ended")
        }
      }

      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      await waitForIceGathering(pc)

      const res = await fetch(`${baseUrl}/api/offer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sdp: pc.localDescription?.sdp,
          type: pc.localDescription?.type,
          pc_id: null,
        }),
      })
      if (!res.ok) throw new Error(`Agent refused the call (${res.status})`)
      const answer = await res.json()
      await pc.setRemoteDescription(answer)
    } catch (e) {
      cleanup()
      const msg = e instanceof Error ? e.message : "Could not connect"
      setErrorMsg(
        msg.includes("Permission") || msg.includes("denied")
          ? "Microphone permission denied."
          : `${msg}. Is the agent running on ${baseUrl}?`,
      )
      setStatus("error")
    }
  }, [baseUrl, cleanup, startAnalyser, endCall])

  // Call timer.
  useEffect(() => {
    if (status !== "active") return
    setDurationSec(0)
    const id = window.setInterval(() => setDurationSec((d) => d + 1), 1000)
    return () => clearInterval(id)
  }, [status])

  // Tear down on unmount.
  useEffect(() => cleanup, [cleanup])

  const toggleMute = useCallback(() => {
    const track = localStreamRef.current?.getAudioTracks()[0]
    if (!track) return
    track.enabled = !track.enabled
    setMuted(!track.enabled)
  }, [])

  return (
    <div className="relative flex h-full w-full flex-col items-center justify-between px-6 pb-6 pt-9 text-white">
      {/* hidden sink that actually plays the bot's voice */}
      <audio ref={audioRef} autoPlay playsInline className="hidden" />

      {/* Caller identity */}
      <div className="flex flex-col items-center gap-3 pt-3">
        <div className="relative">
          {(status === "connecting" || status === "active") && (
            <span
              className="absolute inset-0 rounded-full bg-emerald-400/30"
              style={{
                transform: `scale(${1 + botLevel * 0.5})`,
                transition: "transform 90ms linear",
              }}
            />
          )}
          <div
            className={`relative flex size-24 items-center justify-center rounded-full bg-white/10 text-4xl backdrop-blur ${
              status === "idle" ? "animate-pulse" : ""
            }`}
          >
            🌷
          </div>
        </div>
        <p className="text-xl font-semibold">{agentName}</p>
        <p className="text-sm text-white/55">{phone}</p>

        <div className="mt-1 h-4 text-[11px] uppercase tracking-[0.3em] text-white/70">
          {status === "idle" && <span className="text-emerald-300/90">Incoming call</span>}
          {status === "connecting" && <span className="animate-pulse">Connecting…</span>}
          {status === "active" && <span className="text-emerald-300/90">{fmt(durationSec)}</span>}
          {status === "ended" && <span className="text-white/50">Call ended</span>}
          {status === "error" && <span className="text-red-300">Call failed</span>}
        </div>
      </div>

      {/* Speaking visualizer (active call only) */}
      <div className="flex h-12 items-end justify-center gap-1.5">
        {status === "active" &&
          BAR_WEIGHTS.map((w, i) => (
            <span
              key={i}
              className="w-1.5 rounded-full bg-emerald-300/90"
              style={{
                height: `${6 + botLevel * 42 * w}px`,
                transition: "height 100ms ease-out",
              }}
            />
          ))}
        {status === "error" && (
          <p className="px-6 text-center text-xs leading-relaxed text-red-200/90">{errorMsg}</p>
        )}
      </div>

      {/* Call controls */}
      <div className="mb-1 w-full">
        {status === "active" ? (
          <div className="flex items-center justify-center gap-10">
            <button
              type="button"
              onClick={toggleMute}
              aria-label={muted ? "Unmute" : "Mute"}
              className={`flex size-14 items-center justify-center rounded-full backdrop-blur transition active:scale-95 ${
                muted ? "bg-white/80 text-black" : "bg-white/15 text-white hover:bg-white/25"
              }`}
            >
              {muted ? <MicOff className="size-6" /> : <Mic className="size-6" />}
            </button>
            <button
              type="button"
              onClick={() => endCall("ended")}
              aria-label="End call"
              className="flex size-[72px] items-center justify-center rounded-full bg-red-500 shadow-lg shadow-red-500/40 transition hover:bg-red-400 active:scale-95"
            >
              <PhoneOff className="size-7 text-white" />
            </button>
            <div className="flex size-14 items-center justify-center rounded-full bg-white/10 text-white/70 backdrop-blur">
              <Volume2 className="size-6" />
            </div>
          </div>
        ) : (
          <div className="flex justify-center">
            <button
              type="button"
              onClick={connect}
              disabled={status === "connecting"}
              aria-label={status === "error" ? "Retry call" : "Answer call"}
              className={`flex size-[72px] items-center justify-center rounded-full shadow-lg transition active:scale-95 ${
                status === "connecting"
                  ? "cursor-default bg-emerald-500/60"
                  : "animate-bounce bg-emerald-500 shadow-emerald-500/40 hover:bg-emerald-400"
              }`}
            >
              <Phone className="size-7 text-white" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
