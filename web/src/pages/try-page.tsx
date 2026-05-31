import { useEffect, useState } from "react"
import IPhoneMockup from "@/components/ui/iphone-mockup"
import { PhoneCallExperience } from "@/components/phone-call-experience"
import { botWebRtcUrl, fetchOwnerConfig, type OwnerConfig } from "@/lib/api"

// iPhone 15 Pro outer box (logical px): screen 393×852 + 12px bezel each side.
const PHONE_OUTER_W = 417
const PHONE_OUTER_H = 876

export function TryPage() {
  const [config, setConfig] = useState<OwnerConfig | null>(null)
  const [scale, setScale] = useState(0.8)
  const webrtcUrl = botWebRtcUrl()

  useEffect(() => {
    fetchOwnerConfig().then(setConfig).catch(() => setConfig(null))
  }, [])

  // Fit the phone to the viewport height — it's the only thing on the page.
  useEffect(() => {
    const fit = () => setScale(Math.min(0.95, Math.max(0.5, (window.innerHeight - 40) / PHONE_OUTER_H)))
    fit()
    window.addEventListener("resize", fit)
    return () => window.removeEventListener("resize", fit)
  }, [])

  const agentName = config?.display_name ? `${config.display_name}'s agent` : "Your agent"
  const displayPhone = config?.owner_phone || "+1 (415) 555-0100"

  return (
    <div className="flex min-h-screen items-center justify-center bg-neutral-950">
      <div style={{ width: PHONE_OUTER_W * scale, height: PHONE_OUTER_H * scale }}>
        <IPhoneMockup
          model="15-pro"
          color="natural-titanium"
          scale={scale}
          screenBg="radial-gradient(120% 120% at 50% 0%, #20302a 0%, #0b0e0c 62%)"
        >
          <PhoneCallExperience baseUrl={webrtcUrl} agentName={agentName} phone={displayPhone} />
        </IPhoneMockup>
      </div>
    </div>
  )
}
