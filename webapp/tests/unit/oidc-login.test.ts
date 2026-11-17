import { describe, expect, it } from "vitest";

import { callbackUrl, oidcConfig, publicOrigin, safeNext } from "@/lib/auth/oidc";
import { signInError } from "@/lib/auth/oidc-messages";

describe("browser OIDC sign-in", () => {
  it("is configured only when both the issuer and the client id are set", () => {
    expect(oidcConfig({})).toBeNull();
    expect(oidcConfig({ GROVE_OIDC_ISSUER: "https://id.example.com" })).toBeNull();
    const config = oidcConfig({
      GROVE_OIDC_ISSUER: "https://id.example.com/realms/grove",
      GROVE_OIDC_CLIENT_ID: "grove-web",
    });
    expect(config?.issuer.host).toBe("id.example.com");
    expect(config?.clientSecret).toBeNull();
  });

  it("returns only to a same-site path, never another origin", () => {
    expect(safeNext("/w/abc")).toBe("/w/abc");
    expect(safeNext("//evil.example/x")).toBe("/");
    expect(safeNext("https://evil.example/")).toBe("/");
    expect(safeNext(null)).toBe("/");
  });

  it("calls back to this server's own route", () => {
    expect(callbackUrl("https://grove.example.com")).toBe("https://grove.example.com/api/auth/oidc/callback");
  });

  it("names the origin the browser reached, not the bind address the server sees behind a proxy", () => {
    // The incident: standalone Next reported its own `https://0.0.0.0:3000`
    // while the browser had come through a reverse proxy at the public name.
    const proxied = new Headers({ "x-forwarded-host": "grove.example.com", "x-forwarded-proto": "https" });
    expect(callbackUrl(publicOrigin(proxied, "https://0.0.0.0:3000"))).toBe("https://grove.example.com/api/auth/oidc/callback");
    // A chain of proxies: the first value is the client-facing one, for both headers.
    const chained = new Headers({ "x-forwarded-host": "grove.example.com, inner.lan", "x-forwarded-proto": "https, http" });
    expect(publicOrigin(chained, "http://0.0.0.0:3000")).toBe("https://grove.example.com");
    // A forwarded host with no forwarded scheme keeps the request's own scheme.
    expect(publicOrigin(new Headers({ "x-forwarded-host": "grove.example.com" }), "http://0.0.0.0:3000")).toBe("http://grove.example.com");
  });

  it("falls back to the request's own origin when nothing stands in front of the server", () => {
    expect(publicOrigin(new Headers(), "http://localhost:3000")).toBe("http://localhost:3000");
    // A forwarded scheme alone does not invent a host.
    expect(publicOrigin(new Headers({ "x-forwarded-proto": "https" }), "http://localhost:3000")).toBe("http://localhost:3000");
  });

  it("no OIDC route builds a redirect from the server's own origin", async () => {
    const { readFileSync, readdirSync } = await import("node:fs");
    const { join } = await import("node:path");
    const root = join(process.cwd(), "app/api/auth/oidc");
    const files = readdirSync(root, { recursive: true, withFileTypes: true })
      .filter((entry) => entry.isFile() && entry.name === "route.ts")
      .map((entry) => join(entry.parentPath, entry.name));
    expect(files.length).toBeGreaterThan(1);
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      // Every read of `nextUrl.origin` must be an argument to `publicOrigin`.
      const bare = source.replace(/publicOrigin\(request\.headers, request\.nextUrl\.origin\)/g, "");
      expect(bare, file).not.toContain("nextUrl.origin");
    }
  });

  it("explains every refusal the routes can send back, and nothing it did not send", () => {
    for (const code of ["oidc_refused", "oidc_expired", "oidc_unreachable", "oidc_not_configured", "daemon_unreachable"]) {
      expect(signInError(code)).toBeTruthy();
    }
    expect(signInError("<script>")).toBeUndefined();
    expect(signInError(null)).toBeUndefined();
  });
});

describe("the routes and the login page speak one error vocabulary", () => {
  it("every code a route redirects with has words on the login page", async () => {
    const { readFileSync } = await import("node:fs");
    const routes = ["start", "callback"].map((name) =>
      readFileSync(`app/api/auth/oidc/${name}/route.ts`, "utf8"),
    );
    const codes = new Set(routes.flatMap((src) => [...src.matchAll(/"(oidc_[a-z_]+|daemon_unreachable)"/g)].map((m) => m[1])));
    expect(codes.size).toBeGreaterThan(3);
    for (const code of codes) expect(signInError(code), code).toBeTruthy();
  });
});

describe("the callback hands the library a real URL", () => {
  it("source census: the route parses request.url, never passes request.nextUrl", async () => {
    // `request.nextUrl` is Next's URL-like `NextURL`, and oauth4webapi checks
    // `instanceof URL` before reading a parameter, so passing it refused every
    // live login as "refused" (2026-10-22). A replay with a bogus code failed
    // at the same line, which is why nothing before the live attempt saw it.
    const { readFileSync } = await import("node:fs");
    const source = readFileSync("app/api/auth/oidc/callback/route.ts", "utf8");
    expect(source).toContain("new URL(request.url)");
    expect(source).not.toMatch(/complete\(\s*request\.nextUrl/);
  });

  it("the library rejects a URL-like that is not a URL, which is what the census guards against", async () => {
    const oauth = await import("oauth4webapi");
    const server = { issuer: "https://id.example.com" } as import("oauth4webapi").AuthorizationServer;
    const client = { client_id: "grove" } as import("oauth4webapi").Client;
    const real = new URL("https://grove.example.com/api/auth/oidc/callback?code=abc&state=s1");
    expect(() => oauth.validateAuthResponse(server, client, real, "s1")).not.toThrow();
    // A duck-typed URL (what NextURL is to the library): same fields, wrong class.
    const urlLike = { href: real.href, searchParams: real.searchParams, origin: real.origin } as unknown as URL;
    expect(() => oauth.validateAuthResponse(server, client, urlLike, "s1")).toThrow(/URLSearchParams, or URL/);
  });
});

describe("a provider that advertises the iss response parameter and does not send it", () => {
  it("is validated without the parameter, and still against the parameter when it is sent", async () => {
    const oauth = await import("oauth4webapi");
    const { issParameterAsSent } = await import("@/lib/auth/oidc");
    const server = {
      issuer: "https://id.example.com",
      authorization_response_iss_parameter_supported: true,
    } as import("oauth4webapi").AuthorizationServer;
    const client = { client_id: "grove" } as import("oauth4webapi").Client;
    // The live refusal: the advertisement without the parameter.
    const bare = new URL("https://grove.example.com/api/auth/oidc/callback?code=abc&state=s1");
    expect(() => oauth.validateAuthResponse(server, client, bare, "s1")).toThrow(/"iss" \(issuer\) missing/);
    expect(() => oauth.validateAuthResponse(issParameterAsSent(server, bare), client, bare, "s1")).not.toThrow();
    // When the provider does send it, the value still has to match the issuer.
    const wrongIss = new URL("https://grove.example.com/api/auth/oidc/callback?code=abc&state=s1&iss=https://evil.example");
    expect(() => oauth.validateAuthResponse(issParameterAsSent(server, wrongIss), client, wrongIss, "s1")).toThrow(/iss/);
    const rightIss = new URL("https://grove.example.com/api/auth/oidc/callback?code=abc&state=s1&iss=https://id.example.com");
    expect(() => oauth.validateAuthResponse(issParameterAsSent(server, rightIss), client, rightIss, "s1")).not.toThrow();
    // A provider that never advertised it is left alone.
    const plain = { issuer: "https://id.example.com" } as import("oauth4webapi").AuthorizationServer;
    expect(issParameterAsSent(plain, bare)).toBe(plain);
  });
});
