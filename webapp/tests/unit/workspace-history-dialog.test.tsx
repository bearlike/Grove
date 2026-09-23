import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type {
  ProgressEntryView,
  WorkspaceHistoryView,
} from "@/lib/grove/api";

/**
 * WHAT THIS FILE CAN AND CANNOT SEE, stated up front because the boundary is
 * not obvious and assuming otherwise produces assertions that pass for the
 * wrong reason.
 *
 * Radix mounts `DialogContent` only while the dialog is OPEN, and a static
 * render is always closed — so nothing inside the dialog exists in this
 * markup. Asserting on a timeline row here would be asserting on a string
 * that is absent for a reason unrelated to whether the row is correct.
 *
 * So this file owns exactly the half SSR can answer: **whether the trigger
 * renders at all**, which is the decision with the real failure mode (an
 * affordance opening onto nothing) and the one that is pure data. The
 * dialog's contents are the browser's to verify.
 */

const history = vi.hoisted(() => ({ data: undefined as unknown }));

vi.mock("@/lib/grove/hooks", () => ({
  useWorkspaceHistory: () => history,
}));

const { WorkspaceHistoryDialog } = await import(
  "@/components/grove/workspace/history-dialog"
);

function claim(over: Partial<ProgressEntryView> = {}): ProgressEntryView {
  return {
    recorded_at: "2026-09-14T03:00:00Z",
    phase: "build",
    blocked: false,
    note: "wiring the store",
    ticket_key: null,
    ...over,
  };
}

function recorded(
  over: Partial<WorkspaceHistoryView> = {},
): WorkspaceHistoryView {
  return { name: null, names: [], progress: [], tickets: [], ...over };
}

function render(data: unknown): string {
  history.data = data;
  return renderToStaticMarkup(<WorkspaceHistoryDialog workspaceId="ws1" repoRoot="/repo" />);
}

const NAME = {
  workspace_id: "ws1",
  title: "persist titles",
  description: null,
  repo_root: "/repo",
  first_seen: "2026-09-14T03:00:00Z",
  last_seen: "2026-09-14T03:00:00Z",
  deleted_at: null,
};

describe("the public Info tab history gate", () => {
  it("does not fetch or render recorded history without a repository capability", async () => {
    const source = await import("node:fs/promises").then((fs) =>
      fs.readFile(new URL("../../components/grove/workspace/info-tab.tsx", import.meta.url), "utf8"),
    );

    expect(source).toContain("useWorkspaceHistory(repoRoot === null ? null : state.id)");
    expect(source).toContain("{repoRoot !== null && <WorkspaceHistoryDialog workspaceId={state.id} repoRoot={repoRoot} />}");
  });
});

describe("the workspace history trigger", () => {
  it("does not render when nothing was recorded", () => {
    // EVERY workspace predating the store is in this state, because recording
    // is forward-only. An icon opening onto an empty panel reads as a broken
    // feature, where an absent icon reads as an absent fact.
    expect(render(recorded())).toBe("");
  });

  it("does not render while the read is still in flight", () => {
    expect(render(undefined)).toBe("");
  });

  it("does not render for a recorded NAME alone", () => {
    // The live Task card already shows the current title, so one name is not a
    // history worth an affordance.
    expect(render(recorded({ name: NAME }))).toBe("");
  });

  it("tolerates absent collections on the wire", () => {
    // An optional collection on a wire model generates as OPTIONAL in TS
    // (`default_factory`, per the contracts guide), so the component must read
    // absent and empty as the same thing rather than throwing on undefined.
    expect(render({ name: null })).toBe("");
  });

  it("renders with an accessible name once a claim exists", () => {
    const markup = render(recorded({ progress: [claim()] }));
    expect(markup).toContain('data-testid="workspace-history-trigger"');
    expect(markup).toContain('aria-label="Change history"');
  });

  it("floors its hit area in px, because a pointer is not type", () => {
    // `size-6` is 19.2px at this app's 80% density root. Text size and hit area
    // are separate decisions; this is the one legitimate px floor here.
    const markup = render(recorded({ progress: [claim()] }));
    expect(markup).toContain("min-h-[24px]");
    expect(markup).toContain("min-w-[24px]");
  });

  it("renders for a recorded ticket with no progress claim", () => {
    const markup = render(
      recorded({
        tickets: [
          {
            ticket_key: "gitea:687",
            provider: "gitea",
            ticket_id: "687",
            kind: "issue",
            first_seen: "2026-09-14T03:00:00Z",
            last_seen: "2026-09-14T03:00:00Z",
          },
        ],
      }),
    );
    expect(markup).toContain('data-testid="workspace-history-trigger"');
  });

  it("renders for a rename even with no progress claim", () => {
    const markup = render(
      recorded({
        name: NAME,
        names: [
          { title: "persist titles", description: null, recorded_at: "2026-09-14T03:00:01Z" },
          { title: "c618ba38b7", description: null, recorded_at: "2026-09-14T03:00:00Z" },
        ],
      }),
    );
    expect(markup).toContain('data-testid="workspace-history-trigger"');
  });

  it("keeps the dialog itself closed in a static render", () => {
    // Pins the boundary this file's own header describes, so a later reader
    // does not add a content assertion that can only ever pass vacuously.
    const markup = render(recorded({ progress: [claim()] }));
    expect(markup).toContain('data-state="closed"');
    expect(markup).not.toContain('data-testid="workspace-history-dialog"');
  });
});
