import { Link } from "react-router-dom"
import { ArrowRight } from "lucide-react"
import { useScreenSize } from "@/hooks/use-screen-size"
import { PixelTrail } from "@/components/ui/pixel-trail"
import { GooeyFilter } from "@/components/ui/gooey-filter"
import { Button } from "@/components/ui/button"

export function HeroSection() {
  const screenSize = useScreenSize()

  return (
    <section className="relative flex min-h-[92vh] flex-col items-center justify-center overflow-hidden text-center">
      {/* post-expressionist painting backdrop */}
      <div
        className="absolute inset-0 z-0 bg-cover bg-center"
        style={{ backgroundImage: "url(/hero.png)" }}
      />

      {/* painterly scrims for text legibility + fade into the page */}
      <div className="pointer-events-none absolute inset-0 z-[1] bg-gradient-to-r from-canvas/75 via-canvas/25 to-transparent" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-[1] h-48 bg-gradient-to-b from-transparent to-canvas" />

      {/* soft paint-dab trail that follows the cursor */}
      <GooeyFilter id="gooey-filter-pixel-trail" strength={5} />
      <div
        className="absolute inset-0 z-[2]"
        style={{ filter: "url(#gooey-filter-pixel-trail)" }}
      >
        <PixelTrail
          pixelSize={screenSize.lessThan("md") ? 24 : 32}
          fadeDuration={0}
          delay={500}
          pixelClassName="bg-parchment/70"
        />
      </div>

      <div className="relative z-10 mx-auto max-w-3xl px-6">
        <p className="mb-5 inline-flex items-center gap-2 rounded-full border border-sage-700/30 bg-parchment/60 px-4 py-1.5 text-xs font-medium uppercase tracking-[0.2em] text-sage-800 backdrop-blur">
          Personal voicemail agent
        </p>
        <h1 className="text-4xl font-semibold leading-[1.04] text-ink sm:text-6xl lg:text-7xl">
          Can&apos;t pick up?
          <br />
          <span className="bg-gradient-to-r from-sage-700 via-dusk-600 to-mauve-500 bg-clip-text italic text-transparent">
            We gotchu.
          </span>
        </h1>
        <p className="mx-auto mt-6 max-w-xl text-lg text-ink/70">
          Import your contacts and calendar. Your AI assistant answers calls, captures messages, and
          notifies you — in your voice.
        </p>
        <div className="mt-10 flex flex-col items-center justify-center gap-4 sm:flex-row">
          <Button asChild size="lg" className="min-w-[220px]">
            <Link to="/setup">
              Create your free account
              <ArrowRight className="size-4" />
            </Link>
          </Button>
          <Button asChild variant="outline" size="lg" className="min-w-[200px]">
            <Link to="/try">Try a demo call</Link>
          </Button>
        </div>
      </div>
    </section>
  )
}
