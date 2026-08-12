import type { MetadataRoute } from "next";

/**
 * Makes Grove installable, and nothing more.
 *
 * `background_color`/`theme_color` are a SINGLE static value — the Web
 * Manifest spec has no light/dark variant for either field, unlike the
 * `<meta name="theme-color">` tag `app/layout.tsx`'s `viewport` export
 * carries, which does. So this reads the LIGHT `--surface-base` from
 * `app/globals.css` (`oklch(0.95 0.004 286)`) rather than inventing a hex:
 * that token is the shell panel's own datum rung, the same surface the
 * splash screen and the install icon's backdrop are standing in for. Do not
 * hand-pick a different value here without re-deriving it from that file —
 * it is a real theme token, not a design choice made in this one.
 *
 * `icon.svg` and `favicon.ico` already exist and are reused as-is; nothing
 * here generates new art. `favicon.ico`'s four embedded sizes (16/32/48/256)
 * are declared explicitly because the manifest can't introspect an .ico the
 * way a browser's `<link>` resolution does.
 *
 * NO SERVICE WORKER, NO `next-pwa`, NO RUNTIME CACHING — deliberately, and
 * this is the one place that decision needs recording so nobody adds one
 * later thinking it was an oversight. Grove is a LIVE FLEET DASHBOARD: a
 * cached shell serving a workspace's stale status, phase or transcript is a
 * correctness bug, not an offline nicety. `display: "standalone"` is the
 * whole ask — an installable window, not an offline copy of live state.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Grove",
    short_name: "Grove",
    description: "Tend multiple git worktrees like branches in a forest.",
    start_url: "/",
    display: "standalone",
    background_color: "#eeeef1",
    theme_color: "#eeeef1",
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml" },
      { src: "/favicon.ico", sizes: "16x16 32x32 48x48 256x256", type: "image/x-icon" },
    ],
  };
}
