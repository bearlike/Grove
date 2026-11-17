import { withAui } from "@assistant-ui/next";
import type { NextConfig } from "next";

/**
 * `make webapp-bundle` sets this to build the copy that ships inside the Python
 * wheel. `standalone` traces the server into a self-contained folder that runs
 * as `node server.js` with no `npm ci`. Unoptimized images drop the only native
 * module in the trace (sharp's per-platform binaries, 46 MB measured), so ONE
 * pure-JS wheel serves every OS instead of one wheel per platform. The images
 * are local logos, so nothing is lost. The systemd build (`next start` over
 * `.next`) leaves this unset and is unchanged.
 */
const bundled = process.env.GROVE_WEBAPP_BUNDLE === "1";

const nextConfig: NextConfig = {
  ...(bundled ? { output: "standalone", images: { unoptimized: true } } : {}),
  /**
   * The e2e suite drives `next dev`, and Next's floating dev badge sits in the
   * bottom-left corner — over the collapsed rail's account menu, which is
   * where `/fleet` leaves it. Playwright then reports `<nextjs-portal>
   * intercepts pointer events` for a control production never covers. The
   * badge is dev chrome, not the app, so the harness turns it off.
   */
  ...(process.env.GROVE_E2E ? { devIndicators: false } : {}),
  /**
   * Next 16 blocks cross-origin requests for `/_next/*` dev resources, and it
   * counts a bare IP or hostname as cross-origin even when it is this same
   * server. Grove is routinely opened from somewhere other than `localhost` —
   * a LAN address, the reverse proxy's hostname, or an automated browser — and
   * the failure is nasty to diagnose: the HTML renders, three script chunks
   * 403, and the page silently never hydrates.
   *
   * Dev-only; the production build does not consult this.
   *
   * The extra origins come from the environment rather than a literal list
   * because this repo mirrors to a public remote, and a deployment's own
   * hostname is exactly the kind of host detail that must not be committed.
   * Set `GROVE_DEV_ORIGINS` to a comma-separated list (wildcards allowed, e.g.
   * `*.example.internal`) wherever you run `next dev`.
   */
  /**
   * The file viewer's DWG/DWF renderer imports `@flyfish-dev/cad-viewer`, which
   * is AGPL-3.0-only. Grove is MIT and ships its bundle in a wheel, so that one
   * runtime is swapped for a stub and never reaches the build. Every other
   * renderer is untouched. Delete this alias (and stop removing `wasm/cad` in
   * `assets:viewer`) to turn CAD on in a deployment that accepts the licence.
   */
  turbopack: {
    resolveAlias: { "@flyfish-dev/cad-viewer": "./lib/grove/cad-viewer-stub.ts" },
  },
  allowedDevOrigins: [
    "127.0.0.1",
    "localhost",
    ...(process.env.GROVE_DEV_ORIGINS ?? "")
      .split(",")
      .map((origin) => origin.trim())
      .filter(Boolean),
  ],
};

export default withAui(nextConfig);
