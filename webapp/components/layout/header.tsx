import Link from "next/link";
import { Github } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "./theme-toggle";

const REPO_URL = "https://github.com/bearlike/Grove";

/**
 * App-wide header. Sticky, translucent on scroll, divides the brand
 * group from the actions group with a fixed-height row (h-14) and a
 * subtle bottom border. All interactive elements compose Button so
 * focus rings, hover behavior, and tap targets stay consistent.
 */
export function Header() {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/80 backdrop-blur-md supports-[backdrop-filter]:bg-background/70">
      <div className="mx-auto flex h-14 max-w-screen-xl items-center justify-between gap-3 px-4">
        <Link
          href="/"
          aria-label="Grove home"
          className="group flex items-center gap-2.5 rounded-md px-1 -mx-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <span
            aria-hidden
            className="grid h-8 w-8 place-items-center rounded-md bg-primary text-primary-foreground font-bold tracking-tight shadow-sm transition-transform group-hover:rotate-[-3deg]"
          >
            G
          </span>
          <span className="flex flex-col leading-none">
            <span className="text-base font-semibold tracking-tight">Grove</span>
            <span className="hidden text-[11px] text-muted-foreground sm:inline">
              workspace dashboard
            </span>
          </span>
        </Link>
        <nav className="flex items-center gap-1.5" aria-label="Site actions">
          <Button asChild variant="ghost" size="icon-sm" aria-label="Open Grove on GitHub">
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
              <Github />
            </a>
          </Button>
          <span aria-hidden className="mx-0.5 hidden h-5 w-px bg-border sm:block" />
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
