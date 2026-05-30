import { BrowserRouter, Route, Routes } from "react-router-dom"
import { LandingPage } from "@/pages/landing-page"
import { SetupPage } from "@/pages/setup-page"
import { TryPage } from "@/pages/try-page"

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/try" element={<TryPage />} />
      </Routes>
    </BrowserRouter>
  )
}
