import type { Metadata, Viewport } from "next";

import { Providers } from "@/components/grove/providers";
import { GeistMono, GeistSans } from "./fonts";
import { TITLE_TEMPLATE } from "./title";

import "./globals.css";

/**
 * `template` is what makes every other route's title one word long.
 *
 * A page exports the NOUN and this appends the product, so no page can spell the
 * suffix differently and none of them has to remember it. `default` is the
 * fallback for routes that export nothing — and it is deliberately not run
 * through the template, because "Grove | Grove" is what a naive default gives
 * you.
 *
 * ONE RULE BINDS EVERY TITLE BELOW: never a host path, a repo path, or an
 * identifier. A tab title is read over shoulders, screenshotted into bug
 * reports and saved into bookmarks, so it is the least private surface in the
 * app — and an id in it is not even useful, since it names a row rather than a
 * thing. Where the human name is unavailable, the fallback is a static noun.
 */
export const metadata: Metadata = {
  title: { default: "Grove", template: TITLE_TEMPLATE },
  description: "Tend multiple git worktrees like branches in a forest.",
  // Standalone-mode chrome for iOS, which ignores `app/manifest.ts` and reads
  // its own meta tags instead. `capable` is what drops the Safari UI when
  // launched from the home screen; the manifest's `display: "standalone"`
  // is the same ask for every other platform.
  appleWebApp: { capable: true, title: "Grove", statusBarStyle: "default" },
};

/**
 * The live `<meta name="theme-color">`, split light/dark — unlike
 * `app/manifest.ts`'s `theme_color`, this tag DOES support the
 * `prefers-color-scheme` media match, so it is the one place that pair can
 * actually be expressed. Both values are the `--surface-base` rung from
 * `app/globals.css` (light `oklch(0.95 0.004 286)`, dark
 * `oklch(0.155 0.005 286)`) converted to sRGB hex — the shell panel's own
 * datum, not a colour invented for this file.
 */
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#eeeef1" },
    { media: "(prefers-color-scheme: dark)", color: "#0c0c0e" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${GeistSans.variable} ${GeistMono.variable}`}
    >
      <body className="font-sans antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
