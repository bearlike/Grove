/*
 * The webapp's font seam. Geist Sans drives UI chrome; Geist Mono drives
 * code and git identifiers. next/font self-hosts every face at build time —
 * no CDN request, no layout shift (the `geist` package ships the woff2s).
 *
 * The terminal Nerd Font (JetBrains Mono Nerd Font) is appended HERE as
 * a `next/font/local` face exposing `--font-jetbrains-nerd`, which the
 * `@theme` `--font-terminal` token (globals.css) picks up — so it stays
 * scoped to terminal/transcript-code surfaces and never leaks into the app
 * chrome. Keep this file the single place fonts are declared.
 */
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import localFont from "next/font/local";

/*
 * JetBrains Mono Nerd Font, self-hosted. Full glyph coverage — the
 * Nerd Font's private-use icon + powerline ranges are the whole point (agent
 * TUIs print them), so we do NOT subset. Exposed as `--font-jetbrains-nerd`;
 * the `@theme` `--font-terminal` token (globals.css) already resolves to it,
 * so terminal surfaces opt in via the `font-terminal` utility — applied by
 * dropping `JetBrainsMonoNerd.variable` on the terminal subtree only (never
 * <html>), keeping the Nerd Font out of app chrome.
 */
export const JetBrainsMonoNerd = localFont({
  src: [
    { path: "./fonts/JetBrainsMonoNerdFont-Regular.woff2", weight: "400", style: "normal" },
    { path: "./fonts/JetBrainsMonoNerdFont-Bold.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-jetbrains-nerd",
  display: "swap",
});

export { GeistSans, GeistMono };
