import { ChatCircleText, PhoneIncoming, PhoneTransfer, UserSound, type Icon } from "@phosphor-icons/react"

type Stop = { icon: Icon; title: string; body: string }

const STOPS: Stop[] = [
  { icon: PhoneIncoming, title: "Someone calls you", body: "Your phone rings like it always does. Pick up and Gotchu never hears it." },
  { icon: PhoneTransfer, title: "Your carrier forwards it", body: "If you don't answer, your carrier sends the call to your Gotchu line." },
  { icon: UserSound, title: "Gotchu answers", body: "It asks who is calling, what it's about and when to call them back." },
  { icon: ChatCircleText, title: "You get a text", body: "Who called and why lands on your phone, and in your calls list." },
]

export function CallPath() {
  return (
    <ol className="relative grid gap-8 md:grid-cols-4 md:gap-6">
      <span
        aria-hidden
        className="absolute bottom-6 left-6 top-6 w-px bg-line md:bottom-auto md:left-6 md:right-6 md:top-6 md:h-px md:w-auto"
      />
      {STOPS.map(({ icon: StopIcon, title, body }) => (
        <li key={title} className="relative grid grid-cols-[3rem_1fr] gap-4 md:grid-cols-1">
          <span className="grid size-12 place-items-center rounded-full bg-raised text-ink shadow-panel">
            <StopIcon weight="bold" className="size-5" aria-hidden />
          </span>
          <div>
            <h3 className="text-lg font-bold">{title}</h3>
            <p className="mt-1 text-base leading-relaxed text-muted">{body}</p>
          </div>
        </li>
      ))}
    </ol>
  )
}
