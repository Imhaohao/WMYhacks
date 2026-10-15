import { Link, useLocation } from "react-router-dom"
import { Logo } from "@/components/logo"
import { Button } from "@/components/ui/button"
import { loadSession } from "@/lib/session"

function headerAction(pathname: string): { to: string; label: string } | null {
  const target = loadSession() ? { to: "/calls", label: "Your calls" } : { to: "/onboarding", label: "Set up Gotchu" }
  return pathname === target.to ? null : target
}

export function SiteHeader() {
  const { pathname } = useLocation()
  const action = headerAction(pathname)
  return (
    <header className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-4 sm:px-6">
      <Link to="/" aria-label="Gotchu home" className="rounded-control">
        <Logo />
      </Link>
      {action && (
        <Button asChild size="sm" variant="secondary">
          <Link to={action.to}>{action.label}</Link>
        </Button>
      )}
    </header>
  )
}
