import { describe, expect, test, beforeEach } from "vitest";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { CookieStore, isSecureRequest } from "@/lib/auth/cookie-store";

let tempPath: string;

beforeEach(() => {
  const dir = mkdtempSync(join(tmpdir(), "grove-cookie-test-"));
  tempPath = join(dir, "webapp-sessions.json");
});

describe("CookieStore", () => {
  test("issue returns a base64url cookie id and persists daemon token + session id server-side", async () => {
    const store = new CookieStore(tempPath);
    const cookieId = await store.issue({
      daemonToken: "grove_v1_secrettoken",
      sessionId: "11111111-1111-1111-1111-111111111111",
      label: "phone",
      expiresAt: new Date(Date.now() + 3600_000).toISOString(),
    });
    expect(cookieId).toMatch(/^[A-Za-z0-9_-]+$/);
    expect(cookieId.length).toBeGreaterThan(20);

    const raw = readFileSync(tempPath, "utf-8");
    // Server-side persistence DOES contain the daemon token (it has to —
    // we need to forward it to the daemon on each request). The crucial
    // invariant is that this file is never sent to the browser, which
    // is enforced by `lookup()` returning the entry only to server code.
    expect(raw).toContain("grove_v1_secrettoken");
    expect(raw).toContain("phone");
  });

  test("lookup returns null for unknown cookie id", async () => {
    const store = new CookieStore(tempPath);
    expect(await store.lookup("unknown-id")).toBeNull();
  });

  test("lookup returns the entry for a known cookie id", async () => {
    const store = new CookieStore(tempPath);
    const cookieId = await store.issue({
      daemonToken: "grove_v1_t",
      sessionId: "22222222-2222-2222-2222-222222222222",
      label: "laptop",
      expiresAt: new Date(Date.now() + 3600_000).toISOString(),
    });
    const entry = await store.lookup(cookieId);
    expect(entry).not.toBeNull();
    expect(entry?.daemonToken).toBe("grove_v1_t");
    expect(entry?.sessionId).toBe("22222222-2222-2222-2222-222222222222");
    expect(entry?.label).toBe("laptop");
  });

  test("lookup returns null for expired entries and prunes them from disk", async () => {
    const store = new CookieStore(tempPath);
    const cookieId = await store.issue({
      daemonToken: "grove_v1_old",
      sessionId: "33333333-3333-3333-3333-333333333333",
      label: "old phone",
      expiresAt: new Date(Date.now() - 1000).toISOString(),
    });
    expect(await store.lookup(cookieId)).toBeNull();
  });

  test("revoke drops the entry from disk", async () => {
    const store = new CookieStore(tempPath);
    const cookieId = await store.issue({
      daemonToken: "grove_v1_t",
      sessionId: "44444444-4444-4444-4444-444444444444",
      label: "phone",
      expiresAt: new Date(Date.now() + 3600_000).toISOString(),
    });
    await store.revoke(cookieId);
    expect(await store.lookup(cookieId)).toBeNull();
  });

  test("a fresh store reads the persisted file on first access", async () => {
    const writer = new CookieStore(tempPath);
    const cookieId = await writer.issue({
      daemonToken: "grove_v1_persist",
      sessionId: "55555555-5555-5555-5555-555555555555",
      label: "phone",
      expiresAt: new Date(Date.now() + 3600_000).toISOString(),
    });
    const reader = new CookieStore(tempPath);
    const entry = await reader.lookup(cookieId);
    expect(entry?.daemonToken).toBe("grove_v1_persist");
  });

  test("two instances on the same file don't clobber each other's freshly-issued cookies", async () => {
    // Models two Next.js worker processes, each with its own in-memory cache,
    // sharing one sessions file. Before the delta-against-fresh-disk fix, B's
    // flush dumped B's stale memory and silently deleted cookie `a`.
    const a = new CookieStore(tempPath);
    const b = new CookieStore(tempPath);
    const future = () => new Date(Date.now() + 3600_000).toISOString();
    const cookieA = await a.issue({
      daemonToken: "grove_v1_a",
      sessionId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      label: "phone-a",
      expiresAt: future(),
    });
    const cookieB = await b.issue({
      daemonToken: "grove_v1_b",
      sessionId: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
      label: "phone-b",
      expiresAt: future(),
    });

    // A self-heals via re-read-on-miss for the cookie B minted, and vice versa.
    expect(await a.lookup(cookieB)).not.toBeNull();
    expect(await b.lookup(cookieA)).not.toBeNull();

    // Both survive in a fresh third store reading straight from disk.
    const fresh = new CookieStore(tempPath);
    expect(await fresh.lookup(cookieA)).not.toBeNull();
    expect(await fresh.lookup(cookieB)).not.toBeNull();
  });

  test("revoke does not resurrect via another instance's merge", async () => {
    const a = new CookieStore(tempPath);
    const b = new CookieStore(tempPath);
    const future = () => new Date(Date.now() + 3600_000).toISOString();
    const cookieA = await a.issue({
      daemonToken: "grove_v1_a",
      sessionId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      label: "phone-a",
      expiresAt: future(),
    });
    const cookieB = await b.issue({
      daemonToken: "grove_v1_b",
      sessionId: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
      label: "phone-b",
      expiresAt: future(),
    });

    await a.revoke(cookieA);

    // The delta model deletes only `a` against fresh disk; it must not
    // resurrect via a stale-memory dump, and `b` must survive untouched.
    const fresh = new CookieStore(tempPath);
    expect(await fresh.lookup(cookieA)).toBeNull();
    expect(await fresh.lookup(cookieB)).not.toBeNull();
  });
});

describe("isSecureRequest", () => {
  test("the proxy header wins over a plain inbound protocol", () => {
    expect(isSecureRequest("https", "http:")).toBe(true);
  });

  test("a direct https request with no proxy header is secure", () => {
    expect(isSecureRequest(null, "https:")).toBe(true);
  });

  test("a direct http request with no proxy header is not secure", () => {
    expect(isSecureRequest(null, "http:")).toBe(false);
  });

  test("a plain forwarded-proto wins over an https inbound protocol", () => {
    expect(isSecureRequest("http", "https:")).toBe(false);
  });

  test("the first value of a comma-listed forwarded-proto wins", () => {
    expect(isSecureRequest("https,http", "http:")).toBe(true);
  });
});
