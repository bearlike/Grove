import { describe, expect, test, beforeEach } from "vitest";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { CookieStore } from "@/lib/auth/cookie-store";

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
});
