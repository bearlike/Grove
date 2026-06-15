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
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#2d2d2b" },
    { media: "(prefers-color-scheme: light)", color: "#faf9f5" },
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
