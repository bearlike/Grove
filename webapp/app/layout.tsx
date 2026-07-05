import type { Metadata, Viewport } from "next";
import "./globals.css";
import { GeistSans, GeistMono } from "./fonts";
import { Providers } from "./providers";
import { StatusBar } from "@/components/layout/status-bar";

export const metadata: Metadata = {
  title: "Grove",
  description: "Dashboard for Grove workspaces.",
  manifest: "/manifest.webmanifest",
  // Favicons + touch icon are generated from the Grove logo (docs/logos/grove-logo.png)
  // into public/ — the mark on browser tabs, home screens, and the app shell.
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/icon-192.png", type: "image/png", sizes: "192x192" },
      { url: "/icon-512.png", type: "image/png", sizes: "512x512" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
  },
  appleWebApp: { capable: true, title: "Grove", statusBarStyle: "black-translucent" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  // Browser-chrome tint. A meta value can't read a CSS var, so these are the one
  // place a canvas hex is unavoidable — kept in lock-step with the `--background`
  // tokens in globals.css (dark hsl(224 20% 6%), light hsl(220 24% 98%)).
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0c0e14" },
    { media: "(prefers-color-scheme: light)", color: "#f9fafb" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="min-h-dvh bg-background font-sans text-foreground antialiased">
        <Providers>
          {/* Bottom status bar takes 28px; reserve it via padding-bottom. */}
          <div className="pb-7">{children}</div>
          <StatusBar />
        </Providers>
      </body>
    </html>
  );
}
