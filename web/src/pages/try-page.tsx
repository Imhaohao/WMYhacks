import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { ExternalLink, Loader2, Phone } from "lucide-react"
import { SiteHeader } from "@/components/site-header"
import { TextDisperse } from "@/components/ui/text-disperse"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { botWebRtcUrl, fetchOwnerConfig, type OwnerConfig } from "@/lib/api"
import { cn } from "@/lib/utils"

export function TryPage() {
  const [config, setConfig] = useState<OwnerConfig | null>(null)
  const webrtcUrl = botWebRtcUrl()

  useEffect(() => {
    fetchOwnerConfig().then(setConfig).catch(() => setConfig(null))
  }, [])

  const displayPhone = config?.owner_phone || "+14155550100"

  return (
    <div className="bg-wash min-h-screen text-ink">
      <SiteHeader />
      <div className="mx-auto max-w-4xl px-6 py-12">
        <p className="text-center text-sm uppercase tracking-[0.2em] text-sage-700">Live demo</p>
        <h1 className="mt-2 text-center text-4xl font-semibold">Call your agent</h1>
        <p className="mx-auto mt-4 max-w-lg text-center text-ink/60">
          Hover the number — then open the WebRTC client and say hello.
          {config?.display_name ? ` You're set up as ${config.display_name}.` : ""}
        </p>

        <div className="relative mx-auto mt-16 flex min-h-[120px] w-full max-w-2xl items-center justify-center">
          <div
            aria-hidden
            className={cn(
              "pointer-events-none absolute -top-10 left-1/2 size-full -translate-x-1/2 rounded-full",
              "bg-[radial-gradient(ellipse_at_center,rgba(142,160,108,0.25),transparent_55%)]",
              "blur-[40px]",
            )}
          />
          <TextDisperse className="text-3xl text-sage-700 sm:text-5xl md:text-6xl">
            {displayPhone}
          </TextDisperse>
        </div>

        <div className="mt-16 grid gap-6 md:grid-cols-2">
          <Card className="bg-parchment/70 backdrop-blur">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Phone className="size-5 text-dusk-600" />
                Browser call
              </CardTitle>
              <CardDescription>
                Pipecat SmallWebRTC — run{" "}
                <code className="text-sage-700">ENV=local uv run bot-nemotron.py</code> first.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button asChild className="w-full" size="lg">
                <a href={webrtcUrl} target="_blank" rel="noreferrer">
                  Open voice client
                  <ExternalLink className="size-4" />
                </a>
              </Button>
            </CardContent>
          </Card>

          <Card className="bg-parchment/70 backdrop-blur">
            <CardHeader>
              <CardTitle>What to say</CardTitle>
              <CardDescription>A 60-second demo script for judges.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-ink/70">
              <p>1. &quot;Hi, this is Sarah — it&apos;s urgent.&quot;</p>
              <p>2. Give a callback number and subject.</p>
              <p>3. Confirm the read-back, then hang up.</p>
              <p className="pt-2 text-ink/45">Check owner email or server/outbox/actions.jsonl.</p>
            </CardContent>
          </Card>
        </div>

        {!config && (
          <p className="mt-8 flex items-center justify-center gap-2 text-sm text-ink/50">
            <Loader2 className="size-4 animate-spin" />
            Using demo number — complete <Link to="/setup" className="text-sage-700 underline">setup</Link> for yours.
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
