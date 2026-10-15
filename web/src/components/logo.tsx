import { PhoneIncoming } from "@phosphor-icons/react"

export function Logo() {
  return (
    <span className="flex items-center gap-2 text-lg font-bold">
      <PhoneIncoming weight="bold" className="size-5 text-accent" aria-hidden />
      Gotchu
    </span>
  )
}
