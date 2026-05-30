import { cn } from "@/lib/utils"

type LogoProps = {
  className?: string
  /** show the "Gotchu" wordmark next to the mark */
  showWordmark?: boolean
}

/**
 * Gotchu brand mark — a sage→dusk squircle holding a soft voice
 * waveform, signalling "your voice answering" for a personal voicemail agent.
 */
export function Logo({ className, showWordmark = true }: LogoProps) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <svg
        width="34"
        height="34"
        viewBox="0 0 34 34"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        className="shrink-0"
      >
        <defs>
                  <linearGradient id="gotchu-mark" x1="0" y1="0" x2="34" y2="34" gradientUnits="userSpaceOnUse">
                    <stop stopColor="#5c6e44" />
                    <stop offset="1" stopColor="#547a8e" />
                  </linearGradient>
                </defs>
                <rect width="34" height="34" rx="10" fill="url(#gotchu-mark)" />
                {/* voice waveform */}
                <g
                  stroke="#f4efe3"
                  strokeWidth="2.6"
                  strokeLinecap="round"
                >
          <line x1="9" y1="15" x2="9" y2="19" />
          <line x1="13.5" y1="12" x2="13.5" y2="22" />
          <line x1="18" y1="8.5" x2="18" y2="25.5" />
          <line x1="22.5" y1="12" x2="22.5" y2="22" />
          <line x1="27" y1="15" x2="27" y2="19" />
        </g>
      </svg>
      {showWordmark && (
                <span className="font-display text-xl font-semibold tracking-tight text-ink">
          Gotchu
        </span>
      )}
    </span>
  )
}
