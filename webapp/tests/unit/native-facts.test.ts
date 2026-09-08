import { describe, expect, it } from "vitest";

import type { NativeFactsView, WorkspaceActivityView } from "@/lib/grove/api";
import { nativeFacts } from "@/components/grove/workspace/selectors";

/**
 * Owned-stream facts exist only for a native session, and only once its
 * stream has stated them. The selector must read the SAME row every other
 * Info-tab figure reads, and must answer `null` — never a zeroed block — for
 * a terminal session or an older daemon that omits the field.
 */
function activity(sessions: readonly { activity: { native?: NativeFactsView | null } }[]) {
  return { sessions } as unknown as WorkspaceActivityView;
}

const FACTS: NativeFactsView = {
  cost_usd: 0.3375,
  ttft_ms: 1770,
  turn_duration_ms: 5381,
  last_exit_code: 1,
  permission_denials: 0,
};

describe("nativeFacts", () => {
  it("reads the first session's facts", () => {
    expect(
      nativeFacts(
        activity([{ activity: { native: FACTS } }, { activity: { native: { ...FACTS, ttft_ms: 1 } } }]),
      ),
    ).toBe(FACTS);
  });

  it("is null for a terminal session, an omitted field, and no activity", () => {
    expect(nativeFacts(activity([{ activity: { native: null } }]))).toBeNull();
    expect(nativeFacts(activity([{ activity: {} }]))).toBeNull();
    expect(nativeFacts(null)).toBeNull();
  });
});
