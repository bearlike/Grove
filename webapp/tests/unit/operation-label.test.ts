import { describe, expect, it } from "vitest";

import { operationLabel } from "@/lib/grove/adapters";
import type { NativeOperationView } from "@/lib/grove/api";

const T0 = Date.parse("2026-09-23T19:04:27Z");

function op(overrides: Partial<NativeOperationView>): NativeOperationView {
  return { kind: "compacting", started_at: "2026-09-23T19:04:27Z", ...overrides };
}

describe("operationLabel", () => {
  it("counts a compaction up from its start", () => {
    expect(operationLabel(op({}), T0 + 41_000)).toBe("Compacting context · 41s");
    // The real manual compaction this was measured against ran 79 seconds.
    expect(operationLabel(op({}), T0 + 79_000)).toBe("Compacting context · 1m 19s");
  });

  it("counts a retry DOWN to its instant, with the attempt and the provider's reason", () => {
    const retry = op({
      kind: "retrying",
      attempt: 2,
      max_attempts: 10,
      retry_at: "2026-09-23T19:04:35Z",
      detail: "overloaded (529)",
    });
    expect(operationLabel(retry, T0)).toBe("Retrying 2/10 in 8s · overloaded (529)");
    // Past its instant it is being re-sent now — never a countdown frozen at 0s.
    expect(operationLabel(retry, T0 + 9_000)).toBe("Retrying 2/10 now · overloaded (529)");
  });

  it("states only what the provider said", () => {
    // OpenCode's retry carries no cap; Claude's always does.
    expect(operationLabel(op({ kind: "retrying", attempt: 3 }), T0)).toBe("Retrying 3");
    expect(operationLabel(op({ kind: "retrying" }), T0)).toBe("Retrying");
  });

  it("never shows a negative elapsed when the browser clock trails the daemon's", () => {
    expect(operationLabel(op({}), T0 - 5_000)).toBe("Compacting context · 0s");
  });
});
