import { beforeEach, describe, expect, it } from "vitest";

import { GroveProtocolError } from "@/lib/grove/api";
import type {
  DiagramDocumentView,
  DiagramSessionView,
  DiagramStopRequest,
  DiagramUpdateRequest,
  DiagramWriter,
} from "@/lib/grove/api";
import {
  DiagramCollaboration,
  diagramCollaboration,
  resetDiagramCollaborations,
  type DraftStore,
} from "@/lib/grove/runtime/diagram";

/**
 * The persistence machine, exercised where it actually earns its keep: the
 * races. A happy-path save proves nothing here — the whole reason this is a
 * class is coalescing, the generation fence, and the states that must NOT lose
 * a draft.
 */

const SESSION = "a".repeat(32);
const REOPENED = "b".repeat(32);
const revision = (n: number) => String(n).repeat(64).slice(0, 64);

function descriptor(overrides: Partial<DiagramSessionView> = {}): DiagramSessionView {
  return { path: "docs/flow.drawio", session_id: SESSION, mode: "active", ...overrides };
}

function document(
  rev: string,
  xml: string,
  diagram: DiagramSessionView = descriptor(),
): DiagramDocumentView {
  return { diagram, revision: rev, xml };
}

/** A writer whose every call is resolved by the test, so mid-flight edges are reachable. */
class ScriptedWriter implements DiagramWriter {
  readonly updates: DiagramUpdateRequest[] = [];
  readonly stops: DiagramStopRequest[] = [];
  #pending: ((outcome: { ok: DiagramDocumentView } | { err: unknown }) => void)[] = [];

  update(request: DiagramUpdateRequest): Promise<DiagramDocumentView> {
    this.updates.push(request);
    return new Promise((resolve, reject) => {
      this.#pending.push((outcome) =>
        "ok" in outcome ? resolve(outcome.ok) : reject(outcome.err),
      );
    });
  }

  stop(request: DiagramStopRequest): Promise<DiagramDocumentView> {
    this.stops.push(request);
    return Promise.resolve(
      document(revision(9), "<stopped/>", descriptor({ mode: "read_only" })),
    );
  }

  /** Settle the oldest outstanding update and let the microtask queue drain. */
  async settle(outcome: { ok: DiagramDocumentView } | { err: unknown }): Promise<void> {
    const next = this.#pending.shift();
    if (!next) throw new Error("no update in flight");
    next(outcome);
    await flush();
  }

  get inFlight(): number {
    return this.#pending.length;
  }
}

/** Give the machine's internal `await`s a chance to run. */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function memoryStore(): DraftStore & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return {
    map,
    read: (key) => map.get(key) ?? null,
    write: (key, value) => void map.set(key, value),
    clear: (key) => void map.delete(key),
  };
}

function opened(writer: DiagramWriter, store: DraftStore = memoryStore()) {
  const collaboration = new DiagramCollaboration("ws-1", writer, store);
  collaboration.observe(document(revision(1), "<base/>"));
  return collaboration;
}

describe("baseline adoption", () => {
  it("takes the first document as the baseline and asks the editor to load it", () => {
    const collaboration = opened(new ScriptedWriter());
    const snapshot = collaboration.getSnapshot();
    expect(snapshot.baseline).toEqual({ revision: revision(1), xml: "<base/>" });
    expect(snapshot.save).toBe("clean");
    expect(snapshot.loadToken).toBe(1);
  });

  it("NEVER applies a newer server revision by itself", async () => {
    // draw.io emits nothing while a cell label is being typed, so an automatic
    // reload can destroy work this app cannot even see.
    const collaboration = opened(new ScriptedWriter());
    const before = collaboration.getSnapshot().loadToken;
    collaboration.observe(document(revision(2), "<agent-wrote-this/>"));
    const snapshot = collaboration.getSnapshot();
    expect(snapshot.loadToken).toBe(before);
    expect(snapshot.baseline?.xml).toBe("<base/>");
    expect(snapshot.external?.xml).toBe("<agent-wrote-this/>");
    expect(snapshot.save).toBe("conflict");
    await flush();
  });
});

describe("explicit client-wins conflict recovery", () => {
  it("writes the retained draft against the confirmed backend revision", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    const backend = document(revision(2), "<backend/>");
    collaboration.observe(backend);
    collaboration.queue("<mine/>");
    const token = collaboration.getSnapshot().loadToken;
    const result = collaboration.overwriteWithDraft(backend, "<mine/>");
    expect(writer.updates).toEqual([{ session_id: SESSION, expected_revision: revision(2), xml: "<mine/>" }]);
    await writer.settle({ ok: document(revision(3), "<mine/>") });
    expect(await result).toBe(true);
    expect(collaboration.getSnapshot().draft).toBeNull();
    expect(collaboration.getSnapshot().save).toBe("clean");
    expect(collaboration.getSnapshot().loadToken).toBe(token + 1);
  });

  it("retains the draft if another backend write wins after confirmation", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    const backend = document(revision(2), "<backend/>");
    collaboration.observe(backend);
    collaboration.queue("<mine/>");
    const result = collaboration.overwriteWithDraft(backend, "<mine/>");
    await writer.settle({ err: new GroveProtocolError("diagram_conflict", "changed again", 409) });
    expect(await result).toBe(false);
    expect(collaboration.getSnapshot().draft).toBe("<mine/>");
    expect(collaboration.getSnapshot().save).toBe("conflict");
    expect(writer.updates).toHaveLength(1);
  });

  it("refuses a changed draft, read-only target or different collaboration identity", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    const backend = document(revision(2), "<backend/>");
    collaboration.observe(backend);
    collaboration.queue("<mine/>");
    expect(await collaboration.overwriteWithDraft(backend, "<old-draft/>")).toBe(false);
    expect(await collaboration.overwriteWithDraft({ ...backend, diagram: descriptor({ mode: "read_only" }) }, "<mine/>")).toBe(false);
    expect(await collaboration.overwriteWithDraft({ ...backend, diagram: descriptor({ session_id: REOPENED }) }, "<mine/>")).toBe(false);
    expect(writer.updates).toHaveLength(0);
  });
});

describe("saving", () => {
  it("says Saved only for the LATEST acknowledged draft", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);

    collaboration.queue("<v1/>");
    await flush();
    expect(collaboration.getSnapshot().save).toBe("saving");

    // A newer draft arrives while the first is in flight.
    collaboration.queue("<v2/>");
    await writer.settle({ ok: document(revision(2), "<v1/>") });

    // The acknowledgement is for bytes that are no longer the newest, so the
    // state must NOT read clean — this is the one assertion that separates a
    // truthful indicator from a fast-looking lie. It reads `saving`, not
    // `dirty`: the loop has already issued the follow-up write.
    expect(collaboration.getSnapshot().save).not.toBe("clean");
    expect(collaboration.getSnapshot().save).toBe("saving");
    expect(collaboration.getSnapshot().draft).toBe("<v2/>");

    await writer.settle({ ok: document(revision(3), "<v2/>") });
    expect(collaboration.getSnapshot().save).toBe("clean");
    expect(collaboration.getSnapshot().draft).toBeNull();
  });

  it("COALESCES rather than queueing: a burst of autosaves is one extra write", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<v1/>");
    await flush();
    collaboration.queue("<v2/>");
    collaboration.queue("<v3/>");
    collaboration.queue("<v4/>");
    expect(writer.updates).toHaveLength(1);
    await writer.settle({ ok: document(revision(2), "<v1/>") });
    // One follow-up carrying the newest bytes, not three.
    expect(writer.updates).toHaveLength(2);
    expect(writer.updates[1].xml).toBe("<v4/>");
    await writer.settle({ ok: document(revision(3), "<v4/>") });
  });

  it("sends the revision it read, which is what makes the write conditional", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<v1/>");
    await flush();
    expect(writer.updates[0]).toEqual({
      session_id: SESSION,
      expected_revision: revision(1),
      xml: "<v1/>",
    });
    await writer.settle({ ok: document(revision(2), "<v1/>") });
    collaboration.queue("<v2/>");
    await flush();
    // The baseline advanced to what the daemon acknowledged, not to a guess.
    expect(writer.updates[1].expected_revision).toBe(revision(2));
    await writer.settle({ ok: document(revision(3), "<v2/>") });
  });
});

describe("a poll racing a save", () => {
  it("trusts every observation, because the racing read is CANCELLED not filtered", async () => {
    // `useDiagramWriter` awaits `cancelQueries` before each write, so a GET
    // issued before a PUT is never delivered. That is what lets this class
    // treat any revision it did not produce as a real external change — the
    // alternative rules (discard the next read, or remember revisions already
    // seen) drop genuine changes and cannot recognise a revert A -> B -> A.
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<v1/>");
    await flush();
    await writer.settle({ ok: document(revision(2), "<v1/>") });
    expect(collaboration.getSnapshot().save).toBe("clean");

    collaboration.observe(document(revision(1), "<reverted/>"));
    expect(collaboration.getSnapshot().external?.revision).toBe(revision(1));
    expect(collaboration.getSnapshot().save).toBe("conflict");
  });

  it("treats a re-read of its own acknowledged revision as nothing new", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<v1/>");
    await flush();
    await writer.settle({ ok: document(revision(2), "<v1/>") });

    collaboration.observe(document(revision(2), "<v1/>"));
    expect(collaboration.getSnapshot().external).toBeNull();
    expect(collaboration.getSnapshot().save).toBe("clean");
  });
});

describe("refusals keep the draft", () => {
  it("a 409 is a conflict, and the bytes survive it", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<mine/>");
    await flush();
    await writer.settle({ err: new GroveProtocolError("diagram_conflict", "stale", 409) });
    const snapshot = collaboration.getSnapshot();
    expect(snapshot.save).toBe("conflict");
    expect(snapshot.draft).toBe("<mine/>");
    expect(snapshot.baseline?.xml).toBe("<base/>");
  });

  it("a transport failure is NOT a conflict, and nothing retries by itself", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<mine/>");
    await flush();
    await writer.settle({ err: new TypeError("Failed to fetch") });
    expect(collaboration.getSnapshot().save).toBe("error");
    expect(collaboration.getSnapshot().draft).toBe("<mine/>");
    // A lost acknowledgement may follow a written file, so a blind retry could
    // overwrite the very change it raced. Only an explicit retry re-sends.
    expect(writer.updates).toHaveLength(1);
    collaboration.retry();
    await flush();
    expect(writer.updates).toHaveLength(2);
    await writer.settle({ ok: document(revision(2), "<mine/>") });
  });

  it("a queued edit after a conflict does not silently re-send", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<mine/>");
    await flush();
    await writer.settle({ err: new GroveProtocolError("diagram_conflict", "stale", 409) });
    collaboration.queue("<mine-again/>");
    await flush();
    expect(writer.updates).toHaveLength(1);
    expect(collaboration.getSnapshot().draft).toBe("<mine-again/>");
  });
});

describe("stopping", () => {
  it("flushes what it holds before stopping", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<last-edit/>");
    await flush();
    const stopping = collaboration.stopCollaboration();
    await writer.settle({ ok: document(revision(2), "<last-edit/>") });
    await stopping;
    expect(writer.stops[0]).toEqual({
      session_id: SESSION,
      // The stop is fenced against the revision the save just produced, not
      // the one we opened on.
      expected_revision: revision(2),
    });
    expect(collaboration.getSnapshot().save).toBe("stopped");
  });

  it("AWAITS a write already in flight rather than racing past it", async () => {
    // The flush returns the LIVE drain when one is running. Returning early
    // instead would stop the collaboration while a PUT was still outstanding,
    // stranding the reader's last edit behind a document that is now read-only.
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<in-flight/>");
    await flush();
    expect(writer.inFlight).toBe(1);

    let stopped = false;
    const stopping = collaboration.stopCollaboration().then(() => {
      stopped = true;
    });
    await flush();
    expect(stopped).toBe(false);
    expect(writer.stops).toHaveLength(0);

    await writer.settle({ ok: document(revision(2), "<in-flight/>") });
    await stopping;
    expect(writer.stops[0].expected_revision).toBe(revision(2));
  });

  it("marks itself busy while a write is outstanding, so the stop verb can disable", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<v1/>");
    await flush();
    expect(collaboration.getSnapshot().busy).toBe(true);
    await writer.settle({ ok: document(revision(2), "<v1/>") });
    expect(collaboration.getSnapshot().busy).toBe(false);
  });

  it("fences a save that lands after an EXTERNAL stop", async () => {
    // Without advancing the generation on the stop edge, a stale acknowledgement
    // walks the baseline forward and re-activates a document the daemon has
    // already made read-only.
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<pre-stop/>");
    await flush();

    collaboration.observe(document(revision(1), "<base/>", descriptor({ mode: "read_only" })));
    await writer.settle({ ok: document(revision(6), "<pre-stop/>") });

    const snapshot = collaboration.getSnapshot();
    expect(snapshot.save).toBe("stopped");
    expect(snapshot.baseline).toEqual({ revision: revision(1), xml: "<base/>" });
    expect(snapshot.draft).toBe("<pre-stop/>");
  });

  it("an EXTERNAL stop preserves a draft instead of writing or discarding it", () => {
    const writer = new ScriptedWriter();
    const store = memoryStore();
    const collaboration = opened(writer, store);
    collaboration.observe(
      document(revision(1), "<base/>", descriptor({ mode: "read_only" })),
    );
    collaboration.queue("<typed-after-stop/>");
    const snapshot = collaboration.getSnapshot();
    expect(snapshot.save).toBe("stopped");
    expect(snapshot.draft).toBe("<typed-after-stop/>");
    // Read-only means the daemon would refuse it; sending anyway would give the
    // reader an error per keystroke. It is kept, and recoverable.
    expect(writer.updates).toHaveLength(0);
    expect([...store.map.values()].join()).toContain("<typed-after-stop/>");
  });
});

describe("the generation fence", () => {
  it("discards a save that lands after a reopen minted a new identity", async () => {
    const writer = new ScriptedWriter();
    const collaboration = opened(writer);
    collaboration.queue("<pre-reopen/>");
    await flush();

    collaboration.observe(
      document(revision(5), "<reopened/>", descriptor({ session_id: REOPENED })),
    );
    // The in-flight save describes a collaboration that no longer exists.
    await writer.settle({ ok: document(revision(6), "<pre-reopen/>") });

    const snapshot = collaboration.getSnapshot();
    expect(snapshot.baseline).toEqual({ revision: revision(5), xml: "<reopened/>" });
    expect(snapshot.descriptor?.session_id).toBe(REOPENED);
    // The draft is kept and surfaced, never written and never dropped.
    expect(snapshot.draft).toBe("<pre-reopen/>");
    expect(snapshot.save).toBe("conflict");
  });
});

describe("draft recovery", () => {
  it("mirrors a draft so a tab switch cannot destroy it", () => {
    // `WorkPanel` mounts one tab at a time, so clicking Terminal unmounts the
    // editor. The store is what makes that survivable.
    const store = memoryStore();
    const first = opened(new ScriptedWriter(), store);
    first.queue("<half-drawn/>");

    // Restore runs BEFORE the first document lands, which is the real order in
    // the tab: the recovered draft is what the editor opens on.
    const second = new DiagramCollaboration("ws-1", new ScriptedWriter(), store);
    second.restore(descriptor());
    second.observe(document(revision(1), "<base/>"));
    expect(second.getSnapshot().draft).toBe("<half-drawn/>");
    // Same revision: the file has not moved, so the session simply resumes and
    // the next edit saves. Blocking it would be a trap the reader cannot see.
    expect(second.getSnapshot().save).toBe("dirty");
  });

  it("a draft recovered against a revision the file has left is a conflict", () => {
    const store = memoryStore();
    const first = opened(new ScriptedWriter(), store);
    first.queue("<half-drawn/>");

    const second = new DiagramCollaboration("ws-1", new ScriptedWriter(), store);
    second.restore(descriptor());
    second.observe(document(revision(8), "<moved-on/>"));
    expect(second.getSnapshot().draft).toBe("<half-drawn/>");
    expect(second.getSnapshot().save).toBe("conflict");
  });

  it("only discards on an explicit reload, and then adopts the external bytes", () => {
    const store = memoryStore();
    const collaboration = opened(new ScriptedWriter(), store);
    collaboration.queue("<mine/>");
    collaboration.observe(document(revision(4), "<theirs/>"));

    const before = collaboration.getSnapshot().loadToken;
    collaboration.discardDraftAndReload();
    const snapshot = collaboration.getSnapshot();
    expect(snapshot.draft).toBeNull();
    expect(snapshot.baseline).toEqual({ revision: revision(4), xml: "<theirs/>" });
    expect(snapshot.external).toBeNull();
    expect(snapshot.save).toBe("clean");
    expect(snapshot.loadToken).toBe(before + 1);
    expect(store.map.size).toBe(0);
  });
});

describe("the collaboration registry", () => {
  beforeEach(() => resetDiagramCollaborations());

  it("returns ONE instance per workspace, which is what outlives the tab", () => {
    const writer = new ScriptedWriter();
    expect(diagramCollaboration("ws-1", writer, memoryStore())).toBe(
      diagramCollaboration("ws-1", writer, memoryStore()),
    );
    expect(diagramCollaboration("ws-2", writer, memoryStore())).not.toBe(
      diagramCollaboration("ws-1", writer, memoryStore()),
    );
  });
});
