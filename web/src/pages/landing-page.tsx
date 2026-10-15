import { Link } from "react-router-dom"
import { ArrowRight } from "@phosphor-icons/react"
import { CallPath } from "@/components/call-path"
import { MissedCallDemo } from "@/components/missed-call-demo"
import { SiteFooter } from "@/components/site-footer"
import { SiteHeader } from "@/components/site-header"
import { Button } from "@/components/ui/button"

function SetUpButton() {
  return (
    <Button asChild size="lg">
      <Link to="/onboarding">
        Set up Gotchu
        <ArrowRight weight="bold" className="size-4" aria-hidden />
      </Link>
    </Button>
  )
}

export function LandingPage() {
  return (
    <div className="min-h-[100dvh]">
      <SiteHeader />
      <main>
        <section className="mx-auto grid w-full max-w-6xl items-center gap-12 px-4 pb-20 pt-10 sm:px-6 lg:grid-cols-[1.1fr_1fr] lg:gap-16 lg:pb-28 lg:pt-20">
          <div className="max-w-xl">
            <h1 className="text-display">Can&apos;t pick up? We gotchu.</h1>
            <p className="text-lead mt-6 max-w-md">
              When you miss a call, Gotchu answers, asks who&apos;s calling and why, and texts you the answer.
            </p>
            <div className="mt-9">
              <SetUpButton />
            </div>
          </div>
          <MissedCallDemo />
        </section>

        <section aria-labelledby="path-heading" className="bg-sunken/60">
          <div className="mx-auto w-full max-w-6xl px-4 py-20 sm:px-6 lg:py-24">
            <h2 id="path-heading" className="text-title max-w-xl">
              How a missed call reaches you
            </h2>
            <div className="mt-12">
              <CallPath />
            </div>
          </div>
        </section>

        <section className="mx-auto grid w-full max-w-6xl gap-8 px-4 py-20 sm:px-6 lg:grid-cols-[1fr_auto] lg:items-end lg:py-24">
          <div className="max-w-xl">
            <h2 className="text-title">Your number stays yours</h2>
            <p className="text-lead mt-4">
              Gotchu only hears calls you let ring out. You turn it on with one carrier code and turn it off with another.
            </p>
          </div>
          <SetUpButton />
        </section>
      </main>
      <SiteFooter />
    </div>
  )
}
