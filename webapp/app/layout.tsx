import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { StatusBar } from "@/components/layout/status-bar";

export const metadata: Metadata = {
  title: "Grove",
  description: "Read-only dashboard for Grove workspaces.",
  manifest: "/manifest.webmanifest",
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
    <html lang="en" suppressHydrationWarning>
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
