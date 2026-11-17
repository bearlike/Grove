import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { sortedFleetRows } from "@/components/grove/fleet/filter";
import { hasUnread } from "@/components/grove/fleet/unread";
import { SessionMetadata } from "@/components/grove/fleet/workspace-metrics";
import { lastTranscriptIso, replyCount } from "@/lib/grove/adapters";
import { project, snapshot, workspace } from "@/tests/fixtures/fleet";

describe("the unread cue (#919)", () => {
  it("is set only by growth past a count the reader saw", () => {
    expect(hasUnread({ w: 3 }, "w", 4)).toBe(true);
    expect(hasUnread({ w: 3 }, "w", 3)).toBe(false);
  });

  it("treats a never-observed workspace as read, so a fresh browser is not all blue", () => {
    expect(hasUnread({}, "w", 40)).toBe(false);
  });

  it("does not fire when the count shrank (a remap onto a new session)", () => {
    expect(hasUnread({ w: 9 }, "w", 2)).toBe(false);
  });

  it("counts only top-level sessions, never a sub-agent's internal replies", () => {
    const base = workspace({ id: "w", lastEventAt: "2026-08-01T00:00:00Z" });
    const own = { ...base.sessions[0]!, activity: { ...base.sessions[0]!.activity, assistant_replies: 5 } };
    const child = {
      ...own,
      session: { ...own.session, session_id: "child", parent_session_id: own.session.session_id },
      activity: { ...own.activity, assistant_replies: 50 },
    };
    expect(replyCount({ ...base, sessions: [own, child] })).toBe(5);
  });

  it("never feeds ranking: the sort reads nothing from the unread store", () => {
    const filter = readFileSync("components/grove/fleet/filter.ts", "utf8");
    expect(filter).not.toMatch(/unread|replyCount|assistant_replies/);
    // And a reply count alone cannot reorder rows that share an instant.
    const at = "2026-08-01T00:00:00Z";
    const quiet = workspace({ id: "a", lastEventAt: at });
    const chatty = workspace({ id: "b", lastEventAt: at });
    chatty.sessions[0] = { ...chatty.sessions[0]!, activity: { ...chatty.sessions[0]!.activity, assistant_replies: 99 } };
    expect(sortedFleetRows(snapshot([project("g", "/g", [chatty, quiet])])).map((r) => r.workspace.state.id)).toEqual(["a", "b"]);
  });
});

describe("the ledger's two times (#919)", () => {
  it("shows the age and the transcript's last update, each labelled", () => {
    const html = renderToStaticMarkup(
      <SessionMetadata
        workspace={workspace({ id: "w", createdAt: "2026-08-01T00:00:00Z", lastEventAt: "2026-08-09T00:00:00Z" })}
      />,
    );
    expect(html).toContain('data-testid="rail-age"');
    expect(html).toContain('data-testid="rail-updated"');
    expect(html).toContain("Age: ");
    expect(html).toContain("Last update: ");
    expect(html).not.toContain('data-testid="rail-created"');
  });

  it("prints no update for a workspace whose agent has not reported, rather than its creation", () => {
    const bare = workspace({ id: "w", hasSession: false, createdAt: "2026-08-01T00:00:00Z", updatedAt: "2026-08-05T00:00:00Z" });
    expect(lastTranscriptIso(bare)).toBeNull();
    const html = renderToStaticMarkup(<SessionMetadata workspace={bare} />);
    expect(html).toContain('data-testid="rail-age"');
    expect(html).not.toContain('data-testid="rail-updated"');
    expect(html).toContain("No transcript update yet");
  });

  it("dates the update from the transcript, never from the record's write time", () => {
    const renamed = workspace({ id: "w", lastEventAt: "2026-08-02T00:00:00Z", updatedAt: "2026-08-09T00:00:00Z" });
    expect(lastTranscriptIso(renamed)).toBe("2026-08-02T00:00:00Z");
  });
});
