import { describe, expect, it } from "vitest";

import {
  needsShareSessionPin,
  sharePolicyRequest,
  ttlChoice,
  ttlValue,
} from "@/components/grove/workspace/share-card";

describe("project share policy", () => {
  it("offers 1 day, 7 days, 30 days, and never", () => {
    expect(ttlChoice("86400")).toMatchObject({ label: "1 day", seconds: 86_400 });
    expect(ttlChoice("604800")).toMatchObject({ label: "7 days", seconds: 604_800 });
    expect(ttlChoice("2592000")).toMatchObject({ label: "30 days", seconds: 2_592_000 });
    expect(ttlChoice("never")).toMatchObject({ label: "Never", seconds: null });
  });

  it("shows never for a missing or unknown policy expiry", () => {
    expect(ttlValue(null)).toBe("never");
    expect(ttlValue(undefined)).toBe("never");
    expect(ttlValue(42)).toBe("never");
  });

  it("always sends a full replacement", () => {
    expect(sharePolicyRequest(604_800, "new passcode")).toEqual({
      ttl_seconds: 604_800,
      passcode: "new passcode",
    });
    expect(sharePolicyRequest(null, null)).toEqual({
      ttl_seconds: null,
      passcode: null,
    });
  });

  it("pins legacy and drifted links only when a current session exists", () => {
    expect(needsShareSessionPin(null, "current-session")).toBe(true);
    expect(needsShareSessionPin("older-session", "current-session")).toBe(true);
    expect(needsShareSessionPin("current-session", "current-session")).toBe(false);
    expect(needsShareSessionPin(null, null)).toBe(false);
  });
});
