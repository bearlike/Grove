import { describe, expect, it } from "vitest";

import { mutationErrorMessage } from "@/components/grove/providers";
import { connectivityToastAction } from "@/lib/grove/hooks/stream";
import { GroveProtocolError } from "@/lib/grove/api";

/**
 * The one seam every mutation and the stream's connectivity edge share: a
 * daemon failure becomes ONE sentence, and a state CHANGE becomes ONE toast.
 *
 * Both functions are pure and exported for exactly this reason — the mapping
 * is the contract worth pinning, not the `toast.error(...)` call wrapped
 * around it (see `components/grove/providers.tsx` and
 * `lib/grove/hooks/stream.tsx`).
 */
describe("mutationErrorMessage", () => {
  it("prefers the daemon's own explanation", () => {
    const error = new GroveProtocolError("conflict", "The batch already resolved", 409);
    expect(mutationErrorMessage(error)).toBe("The batch already resolved");
  });

  it("falls back to a plain Error's message — a raw network failure included", () => {
    // `fetch` throws a bare `TypeError` when the daemon is unreachable, never
    // a `GroveProtocolError` — this is the "lost connectivity mid-write" case
    // from the bug report, and it must still read as a sentence.
    expect(mutationErrorMessage(new TypeError("Failed to fetch"))).toBe("Failed to fetch");
  });

  it("never renders an empty sentence", () => {
    expect(mutationErrorMessage(new Error(""))).toBe("Something went wrong.");
  });

  it("never throws on something that was never an Error at all", () => {
    expect(mutationErrorMessage("boom")).toBe("Something went wrong.");
    expect(mutationErrorMessage(undefined)).toBe("Something went wrong.");
  });
});

describe("connectivityToastAction", () => {
  it("stays silent on the very first connect — nothing was lost to recover from", () => {
    expect(connectivityToastAction(true, false)).toBeNull();
  });

  it("stays silent when the daemon is already down before any connect", () => {
    // The persistent `ConnectionState` pill in the rail already carries this
    // level; the toast is only for the edge into or out of it.
    expect(connectivityToastAction(false, false)).toBeNull();
  });

  it("announces a genuine drop, once, only after having been connected", () => {
    expect(connectivityToastAction(false, true)).toBe("lost");
  });

  it("celebrates a real recovery, and only a real recovery", () => {
    expect(connectivityToastAction(true, true)).toBe("resumed");
  });
});
