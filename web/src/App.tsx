import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { LandingPage } from "@/pages/landing-page"
import { OnboardingPage } from "@/pages/onboarding-page"
import { SetupPage } from "@/pages/setup-page"
import { TryPage } from "@/pages/try-page"

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route
          path="/setup"
          element={
            import.meta.env.VITE_ONBOARDING_API_URL ? (
              <SetupPage />
            ) : (
              <Navigate to="/onboarding" replace />
            )
          }
        />
        <Route path="/try" element={<TryPage />} />
      </Routes>
    </BrowserRouter>
  )
}
