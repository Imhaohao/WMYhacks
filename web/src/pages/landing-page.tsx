import { Link } from "react-router-dom"
import { SiteHeader } from "@/components/site-header"
import { HeroSection } from "@/components/hero-section"
import { PhoneDemo } from "@/components/phone-demo"
import MultiOrbitSemiCircle, { FeatureCards } from "@/components/ui/multi-orbit-semi-circle"
import { Button } from "@/components/ui/button"

export function LandingPage() {
  return (
    <div className="min-h-screen">
      <SiteHeader />
      <HeroSection />
      <div className="py-12">
        <FeatureCards />
      </div>
      <MultiOrbitSemiCircle />
      <PhoneDemo />
      <section className="bg-parchment py-20 text-center">
        <h2 className="text-3xl font-semibold lg:text-4xl">Ready in five minutes</h2>
        <p className="mx-auto mt-4 max-w-lg text-ink/60">
          Upload contacts, set your notify channels, and talk to your agent in the browser.
        </p>
        <Button asChild size="lg" className="mt-8">
          <Link to="/setup">Create your account</Link>
        </Button>
      </section>
    </div>
  )
}
