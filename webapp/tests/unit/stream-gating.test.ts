import { describe, expect, it } from "vitest";

import {
  commitsFingerprint,
  findWorkspaceActivity,
  peekFromActivity,
  queueFingerprint,
  streamAction,
  type StreamAction,
} from "@/lib/grove/adapters";
import { backstopInterval } from "@/lib/grove/hooks";
import type {
  CommitSummaryView,
  DashboardEvent,
  WorkspaceActivityView,
  WorkspacePeekView,
} from "@/lib/grove/api";
import { project, snapshot, workspace } from "@/tests/fixtures/fleet";

/**
 * The freshness contract: the stream makes a surface live, and an interval is
 * only what a DROPPED stream falls back to.
 *
 * That sentence was in the code as prose for a long time while every interval
 * ran unconditionally beside a healthy stream. These cases pin the three pure
 * decisions that make it true — when an interval runs, what a frame is worth,
 * and which fields a frame can refresh — so the prose cannot drift from the
 * behaviour again without something going red.
 */

const FLEET = snapshot([
  project("grove", "/repos/grove", [workspace({ id: "known" })]),
]);

function activityFor(id: string, fields: Partial<WorkspaceActivityView> = {}): WorkspaceActivityView {
  return { ...workspace({ id }), ...fields };
}

function frame(kind: DashboardEvent["kind"], fields: Partial<DashboardEvent> = {}): DashboardEvent {
  return { kind, seq: 1, detail: {}, ...fields };
}

const COMMIT: CommitSummaryView = {
  sha: "aaaaaaa",
  subject: "first",
  committed_at: "2026-08-11T00:00:00Z",
};

describe("backstopInterval", () => {
  it("switches an interval OFF while the stream is connected", () => {
    expect(backstopInterval(true, 2_000)).toBe(false);
  });

  it("falls back to the documented cadence once the stream drops", () => {
    expect(backstopInterval(false, 2_000)).toBe(2_000);
  });
});

describe("streamAction", () => {
  it("applies a session_activity for a known workspace instead of refetching", () => {
    const fresh = activityFor("known", { dirty_files: 9, observed_at: "2026-08-12T00:00:00Z" });
    const action = streamAction(FLEET, frame("session_activity", { workspace: fresh }));

    expect(action.kind).toBe("apply");
    const applied = action as Extract<StreamAction, { kind: "apply" }>;
    expect(applied.snapshot.projects[0]!.workspaces[0]!.dirty_files).toBe(9);
    expect(applied.workspace.state.id).toBe("known");
  });

  describe("workspace_changed routes on detail.event, never on the frame's shape", () => {
    function lifecycle(verb: string, id = "known"): DashboardEvent {
      return frame("workspace_changed", { workspace_id: id, detail: { event: verb } });
    }

    it("DROPS a killed workspace — the one fact no other frame can express", () => {
      // `poll_once` reports a vanished workspace by ceasing to emit for it,
      // which is silence; only this frame can say "gone".
      const action = streamAction(FLEET, lifecycle("killed"));
      expect(action.kind).toBe("drop");
      const dropped = action as Extract<StreamAction, { kind: "drop" }>;
      expect(dropped.workspaceId).toBe("known");
      expect(findWorkspaceActivity(dropped.snapshot, "known")).toBeNull();
      expect(dropped.snapshot.total_workspaces).toBe(0);
    });

    it("re-reads ONE workspace on updated — title and ticket_refs ride no delta", () => {
      expect(streamAction(FLEET, lifecycle("updated"))).toEqual({
        kind: "resync",
        workspaceId: "known",
      });
    });

    it.each(["created", "paused", "resumed", "respawned", "provisioning", "error"])(
      "ignores %s, because status IS in the fingerprint the next delta carries",
      (verb) => {
        expect(streamAction(FLEET, lifecycle(verb))).toEqual({ kind: "ignore" });
      },
    );

    it.each(["question_answered", "control_invoked"])(
      "ignores the steering ack %s, which changes no state at all",
      (verb) => {
        expect(streamAction(FLEET, lifecycle(verb))).toEqual({ kind: "ignore" });
      },
    );

    it("routes message_sent to queue_changed instead of ignoring it — it CAN move the queue depth", () => {
      // The one steering ack that is an exception: it can push the text onto
      // the harness's queue, which the activity fingerprint never carries.
      expect(streamAction(FLEET, lifecycle("message_sent"))).toEqual({
        kind: "queue_changed",
        workspaceId: "known",
      });
    });

    it("falls back to a full re-read for a verb it does not recognise", () => {
      // Conservative on purpose: an unknown verb costs one request, where
      // guessing at it costs correctness.
      expect(streamAction(FLEET, lifecycle("teleported"))).toEqual({ kind: "refetch" });
    });

    it("falls back to a full re-read when detail carries no event at all", () => {
      // A `killed` must never be INFERABLE from an absent payload: if the
      // daemon's detail shape changes, this has to break loudly rather than
      // start resurrecting — or quietly deleting — workspaces.
      const bare = frame("workspace_changed", { workspace_id: "known" });
      expect(streamAction(FLEET, bare)).toEqual({ kind: "refetch" });
    });

    it("ignores a kill for a workspace it never held", () => {
      expect(streamAction(FLEET, lifecycle("killed", "stranger"))).toEqual({ kind: "ignore" });
    });
  });

  it("refetches for a workspace the snapshot has never seen — an out-of-band create", () => {
    const event = frame("session_activity", { workspace: activityFor("stranger") });
    expect(streamAction(FLEET, event)).toEqual({ kind: "refetch" });
  });

  it("refetches a delta that arrives before any snapshot", () => {
    const event = frame("session_activity", { workspace: activityFor("known") });
    expect(streamAction(null, event)).toEqual({ kind: "refetch" });
  });

  it("adopts a snapshot frame wholesale", () => {
    const action = streamAction(null, frame("snapshot", { snapshot: FLEET }));
    expect(action).toEqual({ kind: "adopt", snapshot: FLEET });
  });

  it("ignores a heartbeat, so a quiet fleet costs nothing", () => {
    expect(streamAction(FLEET, frame("heartbeat"))).toEqual({ kind: "ignore" });
  });

  it("ignores a pane frame, which belongs to the per-workspace stream", () => {
    expect(streamAction(FLEET, frame("pane_snapshot"))).toEqual({ kind: "ignore" });
  });

  it("ignores a stale delta rather than rolling a row back", () => {
    const stale = activityFor("known", { dirty_files: 4, observed_at: "2020-01-01T00:00:00Z" });
    expect(streamAction(FLEET, frame("session_activity", { workspace: stale }))).toEqual({
      kind: "ignore",
    });
  });
});

describe("peekFromActivity", () => {
  const PREVIOUS: WorkspacePeekView = {
    state: workspace({ id: "known" }).state,
    base_ahead: 0,
    base_behind: 0,
    diff_added: 0,
    diff_removed: 0,
    dirty_files: 0,
    recent_commits: [],
    agent_snapshot: "$ grove status\n",
    snapshot_taken_at: "2026-08-11T00:00:00Z",
  };

  it("takes every working-tree counter from the streamed row", () => {
    const next = peekFromActivity(
      activityFor("known", {
        base_ahead: 3,
        base_behind: 1,
        diff_added: 40,
        diff_removed: 7,
        dirty_files: 2,
        recent_commits: [COMMIT],
      }),
      PREVIOUS,
    );

    expect(next.base_ahead).toBe(3);
    expect(next.base_behind).toBe(1);
    expect(next.diff_added).toBe(40);
    expect(next.diff_removed).toBe(7);
    expect(next.dirty_files).toBe(2);
    expect(next.recent_commits).toEqual([COMMIT]);
  });

  it("keeps the pane capture, which no frame on /events can refresh", () => {
    const next = peekFromActivity(activityFor("known"), PREVIOUS);
    expect(next.agent_snapshot).toBe(PREVIOUS.agent_snapshot);
    expect(next.snapshot_taken_at).toBe(PREVIOUS.snapshot_taken_at);
  });
});

describe("commitsFingerprint", () => {
  function fleetWith(fields: Partial<WorkspaceActivityView>) {
    return snapshot([project("grove", "/repos/grove", [activityFor("known", fields)])]);
  }

  it("is stable while nothing about the log moves", () => {
    const one = fleetWith({ recent_commits: [COMMIT] });
    const two = fleetWith({ recent_commits: [COMMIT] });
    expect(commitsFingerprint(one, "known")).toBe(commitsFingerprint(two, "known"));
  });

  it("moves when a commit lands", () => {
    const before = commitsFingerprint(fleetWith({ recent_commits: [COMMIT] }), "known");
    const after = commitsFingerprint(
      fleetWith({ recent_commits: [{ ...COMMIT, sha: "bbbbbbb" }] }),
      "known",
    );
    expect(after).not.toBe(before);
  });

  it("moves when the BASE moves under an untouched tip — a rebase or a fetch", () => {
    const before = commitsFingerprint(fleetWith({ recent_commits: [COMMIT] }), "known");
    const after = commitsFingerprint(
      fleetWith({ recent_commits: [COMMIT], base_behind: 5 }),
      "known",
    );
    expect(after).not.toBe(before);
  });

  it("is empty and stable for a workspace the snapshot has not seen", () => {
    expect(commitsFingerprint(FLEET, "stranger")).toBe("");
    expect(commitsFingerprint(null, "known")).toBe("");
  });
});

describe("queueFingerprint", () => {
  function fleetWith(fields: Partial<WorkspaceActivityView>) {
    return snapshot([project("grove", "/repos/grove", [activityFor("known", fields)])]);
  }

  it("is stable while the depth does not move", () => {
    const one = fleetWith({ queue: { pending: 2 } });
    const two = fleetWith({ queue: { pending: 2 } });
    expect(queueFingerprint(one, "known")).toBe(queueFingerprint(two, "known"));
  });

  it("moves when a message is queued or drained", () => {
    const before = queueFingerprint(fleetWith({ queue: { pending: 1 } }), "known");
    const after = queueFingerprint(fleetWith({ queue: { pending: 2 } }), "known");
    expect(after).not.toBe(before);
  });

  it("is empty and stable for a workspace the snapshot has not seen", () => {
    expect(queueFingerprint(FLEET, "stranger")).toBe("");
    expect(queueFingerprint(null, "known")).toBe("");
  });

  it("is stable but distinct for a workspace reporting no queue field at all", () => {
    // `queue` is absent (rather than a zero-pending object) on a row the tick
    // has not populated — a stable value distinct from "" (unseen workspace)
    // and from any real pending count, so a skip-first-run guard cannot
    // misfire and a real 0-pending tick is never confused with "no data".
    const noQueue = queueFingerprint(fleetWith({}), "known");
    expect(noQueue).toBe(queueFingerprint(fleetWith({}), "known"));
    expect(noQueue).not.toBe("");
    expect(noQueue).not.toBe(queueFingerprint(fleetWith({ queue: { pending: 0 } }), "known"));
  });
});
