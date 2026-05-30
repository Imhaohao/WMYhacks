import { Link } from "react-router-dom"
import { Logo } from "@/components/logo"
import { Button } from "@/components/ui/button"

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-ink/10 bg-canvas/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <Link to="/" aria-label="Gotchu home">
          <Logo />
        </Link>
        <nav className="flex items-center gap-3">
          <Button asChild variant="ghost" size="sm">
            <Link to="/try">Try call</Link>
          </Button>
          <Button asChild size="sm">
            <Link to="/setup">Set up</Link>
          </Button>
        </nav>
      </div>
    </header>
  )
}
