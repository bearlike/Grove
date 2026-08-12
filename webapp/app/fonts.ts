/*
 * The single place fonts are declared.
 *
 * Geist Sans / Geist Mono are what assistant-ui's own site sets, so adopting
 * them is part of adopting their typography rather than a separate choice.
 * `next/font` self-hosts every face at build time — no CDN request and no
 * layout shift.
 *
 * The Nerd Font is deliberately NOT in that set. It is attached to terminal
 * subtrees only, via the `--font-terminal` token in globals.css, so its
 * private-use icon and powerline ranges stay available where agent TUIs print
 * them without dragging a 1 MB face into app chrome.
 */
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import localFont from "next/font/local";

/*
 * JetBrains Mono Nerd Font, self-hosted and deliberately NOT subset: the
 * private-use icon + powerline ranges are the entire reason it is here, and
 * every subsetter drops them.
 *
 * Exposed as `--font-jetbrains-nerd`. Apply `JetBrainsMonoNerd.variable` on the
 * terminal subtree only — never on <html> — and reach the face through the
 * `font-terminal` utility.
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
