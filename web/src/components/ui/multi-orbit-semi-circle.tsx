"use client"
import { useState, useEffect, type ReactNode } from "react"
import {
  Calendar,
  Mail,
  MessageCircle,
  Phone,
  Cloud,
  Contact,
  type LucideIcon,
} from "lucide-react"

export type IntegrationApp = {
  name: string
  description: string
  icon: LucideIcon
  color: string
}

export const INTEGRATION_APPS: IntegrationApp[] = [
  {
    name: "Apple Contacts",
    description: "Recognize callers by name",
    icon: Contact,
    color: "bg-orange-500",
  },
  {
    name: "iMessage",
    description: "Priority context from texts",
    icon: MessageCircle,
    color: "bg-green-500",
  },
  {
    name: "Google Calendar",
    description: "Suggest callback windows",
    icon: Calendar,
    color: "bg-blue-500",
  },
  {
    name: "Gmail",
    description: "Email summaries to you",
    icon: Mail,
    color: "bg-red-500",
  },
  {
    name: "Twilio",
    description: "Answer your phone line",
    icon: Phone,
    color: "bg-dusk-500",
  },
  {
    name: "AWS",
    description: "Persist every voicemail",
    icon: Cloud,
    color: "bg-amber-500",
  },
]

function OrbitIcon({
  app,
  iconSize,
}: {
  app: IntegrationApp
  iconSize: number
}) {
  const Icon = app.icon
  return (
    <div
      className="group relative flex cursor-pointer flex-col items-center"
      style={{ width: iconSize, height: iconSize }}
    >
      <div
        className={`flex items-center justify-center rounded-2xl shadow-lg ring-2 ring-white/20 transition-transform hover:scale-110 ${app.color}`}
        style={{ width: iconSize, height: iconSize }}
      >
        <Icon className="text-white" size={iconSize * 0.45} strokeWidth={2} />
      </div>
      <div className="pointer-events-none absolute bottom-[calc(100%+10px)] hidden w-36 rounded-lg bg-sage-900 px-3 py-2 text-xs text-parchment shadow-xl group-hover:block">
        <p className="font-semibold">{app.name}</p>
        <p className="mt-0.5 text-parchment/60">{app.description}</p>
        <div className="absolute left-1/2 top-full h-2 w-2 -translate-x-1/2 rotate-45 bg-sage-900" />
      </div>
    </div>
  )
}

function SemiCircleOrbit({
  radius,
  centerX,
  centerY,
  apps,
  iconSize,
}: {
  radius: number
  centerX: number
  centerY: number
  apps: IntegrationApp[]
  iconSize: number
}) {
  const count = apps.length
  if (count === 0) return null

  return (
    <>
      {apps.map((app, index) => {
        const angle = count === 1 ? 90 : (index / (count - 1)) * 180
        const x = radius * Math.cos((angle * Math.PI) / 180)
        const y = radius * Math.sin((angle * Math.PI) / 180)

        return (
          <div
            key={app.name}
            className="absolute flex flex-col items-center"
            style={{
              left: `${centerX + x - iconSize / 2}px`,
              top: `${centerY - y - iconSize / 2}px`,
              zIndex: 5,
            }}
          >
            <OrbitIcon app={app} iconSize={iconSize} />
          </div>
        )
      })}
    </>
  )
}

export default function MultiOrbitSemiCircle() {
  const [size, setSize] = useState({ width: 0, height: 0 })

  useEffect(() => {
    const updateSize = () => setSize({ width: window.innerWidth, height: window.innerHeight })
    updateSize()
    window.addEventListener("resize", updateSize)
    return () => window.removeEventListener("resize", updateSize)
  }, [])

  const baseWidth = Math.min(size.width * 0.9, 720)
  const centerX = baseWidth / 2
  const centerY = baseWidth * 0.48

  const iconSize =
    size.width < 480
      ? Math.max(28, baseWidth * 0.07)
      : size.width < 768
        ? Math.max(32, baseWidth * 0.08)
        : Math.max(36, baseWidth * 0.09)

  const inner = INTEGRATION_APPS.slice(0, 3)
  const middle = INTEGRATION_APPS.slice(3, 5)
  const outer = INTEGRATION_APPS.slice(5, 6)

  return (
    <section className="relative w-full overflow-hidden py-16">
      <div className="absolute inset-0 flex justify-center">
        <div
          className="-mt-20 h-[800px] w-[800px] rounded-full bg-[radial-gradient(circle_at_center,rgba(142,160,108,0.18),transparent_70%)] blur-3xl"
          style={{ zIndex: 0 }}
        />
      </div>

      <div className="relative z-10 flex flex-col items-center text-center">
        <h2 className="my-4 text-4xl font-semibold lg:text-6xl">Your context, connected</h2>
        <p className="mb-10 max-w-2xl text-ink/60 lg:text-xl">
          Import contacts, calendar, and messages so your agent answers like you would — not like a
          generic bot.
        </p>

        <div className="relative" style={{ width: baseWidth, height: baseWidth * 0.55 }}>
          <SemiCircleOrbit
            radius={baseWidth * 0.18}
            centerX={centerX}
            centerY={centerY}
            apps={inner}
            iconSize={iconSize}
          />
          <SemiCircleOrbit
            radius={baseWidth * 0.32}
            centerX={centerX}
            centerY={centerY}
            apps={middle}
            iconSize={iconSize}
          />
          <SemiCircleOrbit
            radius={baseWidth * 0.46}
            centerX={centerX}
            centerY={centerY}
            apps={outer}
            iconSize={iconSize}
          />
        </div>
      </div>
    </section>
  )
}

export function FeatureCards() {
  const cards: { title: string; body: string; icon: ReactNode }[] = [
    {
      title: "Knows who's calling",
      body: "Upload your contact book — the agent greets Sarah by name, not \"unknown caller.\"",
      icon: <Contact className="size-5 text-sage-700" />,
    },
    {
      title: "Adapts tone live",
      body: "Urgent, casual, or distressed — the voice path shifts wording without breaking flow.",
      icon: <MessageCircle className="size-5 text-sage-700" />,
    },
    {
      title: "Acts after hang-up",
      body: "Email you, queue calendar slots, and persist every call to AWS or local storage.",
      icon: <Cloud className="size-5 text-sage-700" />,
    },
  ]

  return (
    <div className="mx-auto grid max-w-5xl gap-6 px-6 md:grid-cols-3">
      {cards.map((c) => (
        <div
          key={c.title}
          className="rounded-2xl border border-ink/12 bg-parchment p-6 text-left shadow-sm"
        >
          <div className="mb-4 flex size-10 items-center justify-center rounded-xl bg-sage-100">
            {c.icon}
          </div>
          <h3 className="text-lg font-semibold">{c.title}</h3>
          <p className="mt-2 text-sm leading-relaxed text-ink/60">{c.body}</p>
        </div>
      ))}
    </div>
  )
}
