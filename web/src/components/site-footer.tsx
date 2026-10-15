import { GithubLogo } from "@phosphor-icons/react"

export function SiteFooter() {
  return (
    <footer className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-4 py-10 text-sm text-muted sm:flex-row sm:items-center sm:justify-between sm:px-6">
      <p>Started at the YC Voice Agents Hackathon by Jerry Yan, David Wu and alaramartin.</p>
      <a
        href="https://github.com/Imhaohao/WMYhacks"
        className="inline-flex items-center gap-1.5 rounded-control font-semibold text-ink hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <GithubLogo weight="bold" className="size-4" aria-hidden />
        Source on GitHub
      </a>
    </footer>
  )
}
