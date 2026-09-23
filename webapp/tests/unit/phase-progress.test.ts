import { describe, expect, it } from "vitest";

import type { PhaseView, ProgressEntryView, TodoProgressView } from "@/lib/grove/api";
import {
  STALE_REPORT_MS,
  activeIndex,
  inPhaseProgress,
  phaseEnteredAt,
  reportIsStale,
  stepIsLive,
  stepState,
} from "@/lib/grove/adapters/phase-progress";

/**
 * The phase track's whole reading of the wire lives in these six functions, and
 * every one of them decides something a screenshot cannot check: which step is
 * current, whether a checklist licenses a fraction at all, which of several
 * same-phase rows started the clock. The fixtures below are written so a wrong
 * answer cannot reach the right outcome through another branch — a run of rows
 * that would return a different instant under "newest" and under "oldest in the
 * list", a todo that is genuinely zero-of-five beside one that is empty, a
 * staleness case sitting exactly on the threshold.
 */
const phaseView = (over: Partial<PhaseView> = {}): PhaseView => ({
  phase: "build",
  note: null,
  blocked: false,
  updated_at: "2026-09-14T12:00:00Z",
  index: 2,
  total: 6,
  tickets: [],
  ...over,
});

const todo = (over: Partial<TodoProgressView> = {}): TodoProgressView => ({
  total: 0,
  completed: 0,
  in_progress: 0,
  pending: 0,
  ...over,
});

const entry = (over: Partial<ProgressEntryView> = {}): ProgressEntryView => ({
  recorded_at: "2026-09-14T12:00:00Z",
  phase: "build",
  blocked: false,
  note: null,
  ticket_key: null,
  ...over,
});

describe("stepState", () => {
  it("reads a step before the reported index as passed through", () => {
    expect(stepState(0, 2)).toBe("complete");
    expect(stepState(1, 2)).toBe("complete");
  });

  it("reads the reported index itself as current, never as complete", () => {
    expect(stepState(2, 2)).toBe("current");
  });

  it("reads a step past the reported index as ahead", () => {
    expect(stepState(3, 2)).toBe("ahead");
    expect(stepState(5, 2)).toBe("ahead");
  });

  it("reads a step as ahead again once a backward report moves the index back", () => {
    // Grove keeps only the LATEST claim, so an agent that reports `plan`
    // after `verify` is not a fault to flag — the track is a pure function of
    // the current index and step 3 simply reads ahead again. Same step, two
    // positions, two answers.
    expect(stepState(3, 4)).toBe("complete");
    expect(stepState(3, 1)).toBe("ahead");
  });
});

describe("activeIndex", () => {
  it.each([
    ["scope" as const, 0],
    ["plan" as const, 1],
    ["build" as const, 2],
    ["verify" as const, 3],
    ["deliver" as const, 4],
  ])("reports %s at its own index so that step is current", (phase, index) => {
    expect(activeIndex(phaseView({ phase, index }))).toBe(index);
  });

  it("puts `handoff` past the end of the list so no step is current", () => {
    // `index` and `total` are deliberately different here: returning the wire's
    // own index for `handoff` would leave step 5 rendering as current on a finished
    // workspace, which is the one case the branch exists for.
    const done = phaseView({ phase: "handoff", index: 5, total: 6 });
    expect(activeIndex(done)).toBe(6);
    expect(stepState(5, activeIndex(done))).toBe("complete");
  });

  it("takes past-the-end from the wire's own total, not from a client constant", () => {
    expect(activeIndex(phaseView({ phase: "handoff", index: 3, total: 4 }))).toBe(4);
  });
});

describe("inPhaseProgress", () => {
  it("licenses no claim when the agent reported no checklist", () => {
    expect(inPhaseProgress(null)).toBeNull();
    expect(inPhaseProgress(undefined)).toBeNull();
  });

  it("licenses no claim from an empty checklist", () => {
    // An empty board is a measurement nobody took. It must NOT read as a zeroed
    // bar, which would say "this phase has made no progress".
    expect(inPhaseProgress(todo({ total: 0, completed: 0 }))).toBeNull();
  });

  it("reports a genuine zero of five as a real fraction of zero", () => {
    // The pair this and the case above form IS the contract: absence is null,
    // and a measured nothing is 0. A client that cannot tell them apart renders
    // a fabricated bar on every workspace that never opened a checklist.
    const measured = inPhaseProgress(todo({ total: 5, completed: 0, pending: 5 }));
    expect(measured).not.toBeNull();
    expect(measured?.fraction).toBe(0);
    expect(measured?.completed).toBe(0);
    expect(measured?.total).toBe(5);
  });

  it("reports the completed fraction and carries both counts through", () => {
    expect(inPhaseProgress(todo({ total: 4, completed: 1, pending: 3 }))).toEqual({
      fraction: 0.25,
      completed: 1,
      total: 4,
    });
    expect(inPhaseProgress(todo({ total: 10, completed: 10 }))).toEqual({
      fraction: 1,
      completed: 10,
      total: 10,
    });
  });
});

describe("phaseEnteredAt", () => {
  const phase = phaseView({ phase: "build" });

  it("has nothing to report with no timeline at all", () => {
    expect(phaseEnteredAt(phase, null)).toBeNull();
    expect(phaseEnteredAt(phase, undefined)).toBeNull();
    expect(phaseEnteredAt(phase, [])).toBeNull();
  });

  it("returns the OLDEST row of the head run, not the newest and not the oldest row held", () => {
    // Three re-reports of one phase (a note update does not restart the clock),
    // then an older phase, then an EVEN OLDER row of the same phase. The three
    // candidate readings all give different answers here: newest is 13:00,
    // oldest-in-the-list is 09:00, and the right answer is 11:00.
    const entered = phaseEnteredAt(phase, [
      entry({ recorded_at: "2026-09-14T13:00:00Z", note: "third note" }),
      entry({ recorded_at: "2026-09-14T12:00:00Z", note: "second note" }),
      entry({ recorded_at: "2026-09-14T11:00:00Z", note: "entered here" }),
      entry({ recorded_at: "2026-09-14T10:00:00Z", phase: "plan" }),
      entry({ recorded_at: "2026-09-14T09:00:00Z" }),
    ]);
    expect(entered).toBe("2026-09-14T11:00:00Z");
  });

  it("skips a ticket row inside the run without letting it end the run", () => {
    // A ticket claim is a claim about a ticket. Folding it in either way is
    // wrong: counted, one ticket's phase would reset the workspace clock;
    // treated as a disagreement, it would truncate the run at 13:00.
    const entered = phaseEnteredAt(phase, [
      entry({ recorded_at: "2026-09-14T14:00:00Z" }),
      entry({ recorded_at: "2026-09-14T13:30:00Z", phase: "verify", ticket_key: "gitea:692" }),
      entry({ recorded_at: "2026-09-14T13:00:00Z", ticket_key: "gitea:692" }),
      entry({ recorded_at: "2026-09-14T12:00:00Z" }),
      entry({ recorded_at: "2026-09-14T11:00:00Z", phase: "plan" }),
    ]);
    expect(entered).toBe("2026-09-14T12:00:00Z");
  });

  it("reads a backward report as a NEW visit, never reaching back to the earlier one", () => {
    // The agent held `build` this morning, moved to `verify`, and has
    // now reported `build` again. The clock starts at the recent visit;
    // reaching past the intervening rows would claim a phase age of most of a
    // day for work that restarted minutes ago.
    const entered = phaseEnteredAt(phase, [
      entry({ recorded_at: "2026-09-14T16:00:00Z" }),
      entry({ recorded_at: "2026-09-14T15:45:00Z" }),
      entry({ recorded_at: "2026-09-14T15:00:00Z", phase: "verify" }),
      entry({ recorded_at: "2026-09-14T14:00:00Z", phase: "verify" }),
      entry({ recorded_at: "2026-09-14T08:00:00Z" }),
      entry({ recorded_at: "2026-09-14T07:00:00Z" }),
    ]);
    expect(entered).toBe("2026-09-14T15:45:00Z");
  });

  it("reports nothing when the newest row disagrees with the phase being rendered", () => {
    // The timeline is written by a ~1 Hz tick and the phase view by its own
    // read, so the two can disagree for an instant. Answering from a row that
    // does not name this phase would date the phase from another one's visit.
    expect(
      phaseEnteredAt(phase, [
        entry({ recorded_at: "2026-09-14T16:00:00Z", phase: "verify" }),
        entry({ recorded_at: "2026-09-14T15:00:00Z" }),
      ]),
    ).toBeNull();
  });

  it("reports nothing when every row it can see belongs to a ticket", () => {
    expect(
      phaseEnteredAt(phase, [
        entry({ recorded_at: "2026-09-14T16:00:00Z", ticket_key: "gitea:692" }),
        entry({ recorded_at: "2026-09-14T15:00:00Z", ticket_key: "gitea:693" }),
      ]),
    ).toBeNull();
  });
});

describe("reportIsStale", () => {
  const reportedAt = "2026-09-14T12:00:00Z";
  const reported = Date.parse(reportedAt);
  const phase = phaseView({ updated_at: reportedAt });

  it("never calls an idle workspace's report stale, however old it is", () => {
    // A finished or paused workspace's last report is hours old and perfectly
    // honest; only an agent claiming to be working owes an update.
    expect(reportIsStale(phase, false, reported + STALE_REPORT_MS * 100)).toBe(false);
  });

  it("never calls a `handoff` report stale even while the session is working", () => {
    const done = phaseView({ phase: "handoff", updated_at: reportedAt });
    expect(reportIsStale(done, true, reported + STALE_REPORT_MS * 10)).toBe(false);
  });

  it("leaves a recent report alone", () => {
    expect(reportIsStale(phase, true, reported + 2 * 60 * 1000)).toBe(false);
  });

  it("holds right up to the threshold and flips exactly on it", () => {
    expect(reportIsStale(phase, true, reported + STALE_REPORT_MS - 1)).toBe(false);
    expect(reportIsStale(phase, true, reported + STALE_REPORT_MS)).toBe(true);
    expect(reportIsStale(phase, true, reported + STALE_REPORT_MS + 1)).toBe(true);
  });

  it("stays quiet rather than guessing when the timestamp is unreadable", () => {
    const broken = phaseView({ updated_at: "not a date" });
    expect(reportIsStale(broken, true, reported + STALE_REPORT_MS * 10)).toBe(false);
  });
});

describe("stepIsLive", () => {
  it.each([
    // working, blocked, phase, live
    [true, false, "build" as const, true],
    [false, false, "build" as const, false],
    [true, true, "build" as const, false],
    [false, true, "build" as const, false],
    [true, false, "handoff" as const, false],
    [true, true, "handoff" as const, false],
    [false, false, "handoff" as const, false],
  ])(
    "working=%s blocked=%s phase=%s pulses: %s",
    (working, blocked, phase, live) => {
      expect(stepIsLive(phaseView({ phase, blocked }), working)).toBe(live);
    },
  );

  it("never pulses a blocked step the agent is still nominally working on", () => {
    // The case motion would most misrepresent: nothing is moving, and an
    // animated mark beside the word `blocked` reads as activity.
    expect(stepIsLive(phaseView({ blocked: true }), true)).toBe(false);
  });
});
