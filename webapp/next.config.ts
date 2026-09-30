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
