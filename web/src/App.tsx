import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom"
import { CallsPage } from "@/pages/calls-page"
import { LandingPage } from "@/pages/landing-page"
import { OnboardingPage } from "@/pages/onboarding-page"

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="/calls" element={<CallsPage />} />
        <Route path="/setup" element={<Navigate to="/onboarding" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
