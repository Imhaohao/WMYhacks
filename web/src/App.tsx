import { useEffect, useState } from "react"
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { LandingPage } from "@/pages/landing-page"
import { OnboardingPage } from "@/pages/onboarding-page"
import { SetupPage } from "@/pages/setup-page"
import { TryPage } from "@/pages/try-page"
import { fetchOnboardingStatus, tenantOnboardingConfigured } from "@/lib/api"

function SetupRoute() {
  // SetupPage is the private owner console — it calls owner-only endpoints
  // (/api/owner-config, /api/services, /api/health, contacts upload) that the
  // backend gates behind ALLOW_OWNER_PROVISIONING. Render it only when the API
  // reports private activation is enabled; otherwise send the visitor to the
  // public /onboarding flow so /setup never renders a console that 403s on
  // every call (the case on the public tenant/Vercel deployment).
  const [allowed, setAllowed] = useState<boolean | null>(null)
  useEffect(() => {
    if (!tenantOnboardingConfigured()) {
      setAllowed(false)
      return
    }
    fetchOnboardingStatus()
      .then((status) => setAllowed(Boolean(status.private_activation_enabled)))
      .catch(() => setAllowed(false))
  }, [])
  if (allowed === null) return null
  return allowed ? <SetupPage /> : <Navigate to="/onboarding" replace />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/setup" element={<SetupRoute />} />
        <Route path="/try" element={<TryPage />} />
      </Routes>
    </BrowserRouter>
  )
}
