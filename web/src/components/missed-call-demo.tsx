import type { CSSProperties } from "react"
import { ScreenedCallCard } from "@/components/screened-call-card"
import { EXAMPLE_CALL } from "@/lib/example-call"

function delay(seconds: number): CSSProperties {
  return { "--reveal-delay": `${seconds}s` } as CSSProperties
}

export function MissedCallDemo() {
  return (
    <figure className="relative mx-auto w-full max-w-md lg:mx-0 lg:ml-auto">
      <ScreenedCallCard call={EXAMPLE_CALL} className="reveal" />
      <div className="reveal mt-4 flex justify-end sm:-mr-6" style={delay(0.9)}>
        <p className="max-w-[19rem] rounded-panel rounded-br-md bg-accent px-4 py-3 text-base leading-snug text-control-ink shadow-panel dark:text-surface">
          {EXAMPLE_CALL.owner_text}
        </p>
      </div>
      <figcaption className="sr-only">
        An example screened call from a fictional caller, and the text message Gotchu sends about it.
      </figcaption>
    </figure>
  )
}
