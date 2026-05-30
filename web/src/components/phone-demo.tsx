import { Link } from "react-router-dom"
import { ArrowRight } from "lucide-react"
import { TextDisperse } from "@/components/ui/text-disperse"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function PhoneDemo() {
  return (
    <section className="bg-wash relative flex w-full flex-col items-center overflow-hidden border-y border-ink/10 py-24 text-ink">
      <div
        aria-hidden="true"
        className={cn(
          "pointer-events-none absolute left-1/2 top-1/2 size-full -translate-x-1/2 -translate-y-1/2 rounded-full",
          "bg-[radial-gradient(ellipse_at_center,rgba(142,160,108,0.22),transparent_55%)]",
          "blur-[40px]",
        )}
      />

      <p className="z-10 text-sm font-medium uppercase tracking-[0.2em] text-sage-700">
        Your dedicated line
      </p>
      <h2 className="z-10 mt-3 text-3xl font-semibold lg:text-5xl">Give callers a number that answers</h2>
      <p className="z-10 mt-4 max-w-xl px-6 text-center text-ink/60">
        Hover the number — that&apos;s your agent picking up when you can&apos;t.
      </p>

      <div className="z-10 mt-12 w-full max-w-3xl px-6">
        <TextDisperse className="justify-center gap-1 text-4xl font-semibold text-sage-700 sm:text-6xl lg:text-7xl">
          +18338846848
        </TextDisperse>
      </div>

      <Button asChild size="lg" className="z-10 mt-14">
        <Link to="/setup">
          Claim your number
          <ArrowRight className="size-4" />
        </Link>
      </Button>
    </section>
  )
}
