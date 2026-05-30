import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { Loader2 } from "lucide-react"
import { SiteHeader } from "@/components/site-header"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import IPhoneMockup from "@/components/ui/iphone-mockup"
import { PhoneCallExperience } from "@/components/phone-call-experience"
import { botWebRtcUrl, fetchOwnerConfig, type OwnerConfig } from "@/lib/api"

// iPhone 15 Pro outer box (logical px): screen 393×852 + 12px bezel each side.
// Scaling via transform doesn't shrink the layout box, so we wrap the mockup in
// a container sized to the scaled dimensions to keep the page layout honest.
const PHONE_SCALE = 0.8
const PHONE_OUTER_W = 417
const PHONE_OUTER_H = 876

export function TryPage() {
  const [config, setConfig] = useState<OwnerConfig | null>(null)
  const webrtcUrl = botWebRtcUrl()

  useEffect(() => {
    fetchOwnerConfig().then(setConfig).catch(() => setConfig(null))
  }, [])

  const agentName = config?.display_name ? `${config.display_name}'s agent` : "Your agent"
  const displayPhone = config?.owner_phone || "+1 (415) 555-0100"

  return (
    <div className="bg-wash min-h-screen text-ink">
      <SiteHeader />
      <div className="mx-auto max-w-4xl px-6 py-12">
        <p className="text-center text-sm uppercase tracking-[0.2em] text-sage-700">Live demo</p>
        <h1 className="mt-2 text-center text-4xl font-semibold">Call your agent</h1>
        <p className="mx-auto mt-4 max-w-lg text-center text-ink/60">
          Tap the green button to answer — talk to your agent like a real phone call.
          {config?.display_name ? ` You're set up as ${config.display_name}.` : ""}
        </p>

        {/* The bot, presented as a real phone call instead of the Pipecat playground */}
        <div className="mt-12 flex flex-col items-center">
          <div
            className="relative"
            style={{ width: PHONE_OUTER_W * PHONE_SCALE, height: PHONE_OUTER_H * PHONE_SCALE }}
          >
            <div
              aria-hidden
              className="pointer-events-none absolute left-1/2 top-1/3 -z-0 size-[120%] -translate-x-1/2 rounded-full bg-[radial-gradient(ellipse_at_center,rgba(142,160,108,0.28),transparent_60%)] blur-[50px]"
            />
            <IPhoneMockup
              model="15-pro"
              color="natural-titanium"
              scale={PHONE_SCALE}
              screenBg="radial-gradient(120% 120% at 50% 0%, #20302a 0%, #0b0e0c 62%)"
              style={{ position: "relative", zIndex: 1 }}
            >
              <PhoneCallExperience baseUrl={webrtcUrl} agentName={agentName} phone={displayPhone} />
            </IPhoneMockup>
          </div>

          <p className="mt-6 text-center text-xs text-ink/50">
            Run <code className="text-sage-700">ENV=local uv run bot-nemotron.py</code> first so the
            agent is listening on <code className="text-sage-700">{webrtcUrl}</code>.
          </p>
        </div>

        {/* Demo script for judges */}
        <Card className="bg-parchment/70 mx-auto mt-14 max-w-md backdrop-blur">
          <CardHeader>
            <CardTitle>What to say</CardTitle>
            <CardDescription>A 60-second demo script for judges.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm text-ink/70">
            <p>1. &quot;Hi, this is Sarah — it&apos;s urgent.&quot;</p>
            <p>2. Give a callback number and subject.</p>
            <p>3. Confirm the read-back, then hang up.</p>
            <p className="pt-2 text-ink/45">Check owner email/SMS or server/aws_store/records.jsonl.</p>
          </CardContent>
        </Card>

        {!config && (
          <p className="mt-8 flex items-center justify-center gap-2 text-sm text-ink/50">
            <Loader2 className="size-4 animate-spin" />
            Using demo number — complete{" "}
            <Link to="/setup" className="text-sage-700 underline">
              setup
            </Link>{" "}
            for yours.
          </p>
        )}

        <div className="mt-12 text-center">
          <Button asChild variant="secondary">
            <Link to="/setup">Back to setup</Link>
          </Button>
        </div>
      </div>
    </div>
  )
}
