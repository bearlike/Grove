import { GroveProtocolError } from "@/lib/grove/api";
import type {
  DiagramDocumentView,
  DiagramSessionView,
  DiagramWriter,
} from "@/lib/grove/api/diagrams";

export type { DiagramWriter };

/**
 * The persistence state machine behind the Diagram tab.
 *
 * The invariant: `baseline` is what the daemon has ACKNOWLEDGED, `draft` is
 * what it has not. "Saved" is only ever said about `baseline`.
 */

export type DiagramSaveState =
  | "clean"
  | "dirty"
  | "saving"
  | "conflict"
  | "error"
  /** Collaboration stopped. A draft is kept for recovery, never written. */
  | "stopped";

export interface DiagramSnapshot {
  readonly descriptor: DiagramSessionView | null;
  readonly baseline: { readonly revision: string; readonly xml: string } | null;
  readonly draft: string | null;
  readonly save: DiagramSaveState;
  /** Newer server bytes, held until the editor is proven clean or the reader decides. */
  readonly external: DiagramDocumentView | null;
  /** Bumped when the editor should load `baseline`. The tab keys its iframe on it. */
  readonly loadToken: number;
  /** A write is outstanding: the stop verb must not race it. */
  readonly busy: boolean;
  readonly lastError: string | null;
}

export interface DraftStore {
  read(key: string): string | null;
  write(key: string, value: string): void;
  clear(key: string): void;
}

const NO_STORE: DraftStore = { read: () => null, write: () => undefined, clear: () => undefined };

/** `sessionStorage`, not `localStorage`: a draft recovered a week later is a conflict pretending to be a rescue. */
export function sessionDraftStore(): DraftStore {
  if (typeof window === "undefined") return NO_STORE;
  const guard = <T,>(run: () => T, fallback: T): T => {
    try {
      return run();
    } catch {
      return fallback;
    }
  };
  return {
    read: (key) => guard(() => window.sessionStorage.getItem(key), null),
    write: (key, value) => guard(() => window.sessionStorage.setItem(key, value), undefined),
    clear: (key) => guard(() => window.sessionStorage.removeItem(key), undefined),
  };
}

export class DiagramCollaboration {
  #snapshot: DiagramSnapshot = {
    descriptor: null,
    baseline: null,
    draft: null,
    save: "clean",
    external: null,
    loadToken: 0,
    busy: false,
    lastError: null,
  };
  #listeners = new Set<() => void>();
  /** The live flush, so a second caller AWAITS the drain instead of returning immediately. */
  #drain: Promise<void> | null = null;
  /** Fences a resolving save. Advanced by a reopen AND by a stop, or a save
   * issued while the file was writable re-activates a stopped document. */
  #generation = 0;
  #recovered: { revision: string; xml: string } | null = null;

  constructor(
    readonly workspaceId: string,
    private readonly writer: DiagramWriter,
    private readonly store: DraftStore = NO_STORE,
  ) {}

  subscribe = (listener: () => void): (() => void) => {
    this.#listeners.add(listener);
    return () => void this.#listeners.delete(listener);
  };

  getSnapshot = (): DiagramSnapshot => this.#snapshot;

  /** Fold in what the server says. A newer revision is recorded as `external`
   * and left there; only `adoptExternal` applies it. */
  observe(document: DiagramDocumentView | null): void {
    if (document === null) return;
    const current = this.#snapshot;
    const descriptor = document.diagram;
    const reopened =
      current.descriptor !== null && current.descriptor.session_id !== descriptor.session_id;
    const stopping = descriptor.mode === "read_only" && current.descriptor?.mode === "active";
    if (reopened || stopping) this.#generation += 1;

    if (current.baseline === null || reopened) {
      this.#set({
        descriptor,
        baseline: { revision: document.revision, xml: document.xml },
        external: null,
        loadToken: current.loadToken + 1,
        save: this.#adoptedSave(current.draft, reopened, document.revision, descriptor),
      });
      return;
    }

    // Every observation is trusted, because a read that raced a write is
    // CANCELLED before the write goes out (`useDiagramWriter`) and so is never
    // delivered. Guessing here instead — a discard-the-next-read rule, or a set
    // of revisions already seen — drops real external changes and cannot tell a
    // legitimate revert (A → B → A) from a stale answer.
    const external = document.revision === current.baseline.revision ? null : document;
    this.#set({
      descriptor,
      external,
      save:
        descriptor.mode === "read_only"
          ? "stopped"
          : external !== null && current.save === "clean"
            ? "conflict"
            : current.save,
    });
  }

  /** A recovered draft against the same revision is a resumed session; anything else is a conflict. */
  #adoptedSave(
    draft: string | null,
    reopened: boolean,
    revision: string,
    descriptor: DiagramSessionView,
  ): DiagramSaveState {
    if (descriptor.mode === "read_only") return "stopped";
    if (draft === null) return "clean";
    return !reopened && this.#recovered?.revision === revision ? "dirty" : "conflict";
  }

  /**
   * Adopt the external document, the caller having PROVEN the editor
   * unmodified (commit the cell edit, export, compare to the load-time
   * capture). This is what makes an agent's edit live rather than a button.
   */
  adoptExternal(): boolean {
    const { external, draft } = this.#snapshot;
    if (external === null || draft !== null) return false;
    this.#set({
      descriptor: external.diagram,
      baseline: { revision: external.revision, xml: external.xml },
      external: null,
      save: external.diagram.mode === "read_only" ? "stopped" : "clean",
      loadToken: this.#snapshot.loadToken + 1,
    });
    return true;
  }

  /** Take editor bytes. Coalescing, not queueing: only the newest draft is worth writing. */
  queue(xml: string): void {
    const current = this.#snapshot;
    if (current.descriptor?.mode === "read_only") {
      this.#set({ draft: xml, save: "stopped" });
      this.#persist(xml);
      return;
    }
    // A refused document stays refused until the reader acts; letting the next
    // autosave clear it would re-send against a revision nobody compared.
    const blocked = current.save === "conflict" || current.save === "error";
    this.#set({
      draft: xml,
      save: blocked ? current.save : current.save === "saving" ? "saving" : "dirty",
    });
    this.#persist(xml);
    if (!blocked) void this.#flush();
  }

  /** Re-send after a transport failure. Never from `conflict`: the same bytes
   * against the same revision can only fail again. */
  retry(): void {
    if (this.#snapshot.draft === null || this.#snapshot.save !== "error") return;
    this.#set({ save: "dirty", lastError: null });
    void this.#flush();
  }

  /** Explicit client-wins recovery against the backend version the user confirmed.
   * This advances the write precondition, not the editor contents. A subsequent
   * backend change still conflicts through the ordinary conditional writer. */
  async overwriteWithDraft(confirmed: DiagramDocumentView, draft: string): Promise<boolean> {
    const current = this.#snapshot;
    if (
      current.busy || current.save !== "conflict" || current.draft !== draft ||
      current.descriptor?.mode !== "active" || confirmed.diagram.mode !== "active" ||
      current.descriptor.session_id !== confirmed.diagram.session_id ||
      current.descriptor.path !== confirmed.diagram.path
    ) return false;
    this.#set({
      baseline: { revision: confirmed.revision, xml: confirmed.xml },
      external: null,
      save: "dirty",
      lastError: null,
    });
    this.#persist(draft);
    await this.#flush();
    if (this.#snapshot.save !== "clean") return false;
    this.#set({ loadToken: this.#snapshot.loadToken + 1 });
    return true;
  }

  discardDraftAndReload(): void {
    const external = this.#snapshot.external;
    this.#generation += 1;
    this.#clearPersisted();
    this.#recovered = null;
    this.#set({
      baseline: external
        ? { revision: external.revision, xml: external.xml }
        : this.#snapshot.baseline,
      draft: null,
      external: null,
      save: this.#snapshot.descriptor?.mode === "read_only" ? "stopped" : "clean",
      lastError: null,
      loadToken: this.#snapshot.loadToken + 1,
    });
  }

  dismissDraft(): void {
    this.#clearPersisted();
    this.#recovered = null;
    this.#set({
      draft: null,
      save: this.#snapshot.descriptor?.mode === "read_only" ? "stopped" : "clean",
      lastError: null,
    });
  }

  /**
   * Stop collaborating, after the queue has actually drained — `#flush` returns
   * the LIVE drain, so this awaits the write in flight instead of racing it. A
   * draft that still cannot be written is kept: a stop is not a promise that
   * everything reached disk.
   */
  async stopCollaboration(): Promise<void> {
    await this.#flush();
    const { descriptor, baseline } = this.#snapshot;
    if (descriptor === null || baseline === null || descriptor.mode === "read_only") return;
    const generation = this.#generation;
    this.#set({ busy: true });
    try {
      const document = await this.writer.stop({
        session_id: descriptor.session_id,
        expected_revision: baseline.revision,
      });
      if (generation !== this.#generation) return;
      this.#generation += 1;
      this.#set({
        descriptor: document.diagram,
        baseline: { revision: document.revision, xml: document.xml },
        save: "stopped",
      });
    } catch (error) {
      this.#set({ save: this.#failureState(error), lastError: messageOf(error) });
    } finally {
      this.#set({ busy: false });
    }
  }

  /** Restore a mirrored draft. A stopped collaboration recovers it for
   * DOWNLOAD only — see `editorSource`. */
  restore(descriptor: DiagramSessionView): void {
    if (this.#snapshot.draft !== null) return;
    const stored = this.store.read(this.#key(descriptor.session_id));
    if (stored === null) return;
    const recovered = parseRecovered(stored);
    if (recovered === null) return;
    this.#recovered = recovered;
    this.#set({
      draft: recovered.xml,
      save: descriptor.mode === "read_only" ? "stopped" : "dirty",
    });
  }

  /** The XML the editor should open on: a resumable draft, else the acknowledged file. */
  editorSource(): string | undefined {
    const { draft, baseline, descriptor, save } = this.#snapshot;
    if (descriptor?.mode === "read_only" || save === "conflict") return baseline?.xml;
    return draft ?? baseline?.xml;
  }

  #flush(): Promise<void> {
    if (this.#drain !== null) return this.#drain;
    const drain = this.#run().finally(() => {
      this.#drain = null;
    });
    this.#drain = drain;
    return drain;
  }

  async #run(): Promise<void> {
    for (;;) {
      const { draft, baseline, descriptor, save } = this.#snapshot;
      if (draft === null || baseline === null || descriptor === null) return;
      if (descriptor.mode === "read_only" || save === "conflict" || save === "error") return;
      const generation = this.#generation;
      this.#set({ save: "saving", busy: true });
      let document: DiagramDocumentView;
      try {
        document = await this.writer.update({
          session_id: descriptor.session_id,
          expected_revision: baseline.revision,
          xml: draft,
        });
      } catch (error) {
        // A fenced result describes a collaboration that no longer exists, so
        // it changes nothing except that this writer is no longer working.
        if (generation !== this.#generation) return this.#set({ busy: false });
        this.#set({ save: this.#failureState(error), lastError: messageOf(error), busy: false });
        return;
      }
      if (generation !== this.#generation) return this.#set({ busy: false });
      const acknowledged = this.#snapshot.draft === draft;
      this.#set({
        descriptor: document.diagram,
        baseline: { revision: document.revision, xml: document.xml },
        external: null,
        lastError: null,
        // Clean ONLY when the acknowledged draft is still the newest one.
        draft: acknowledged ? null : this.#snapshot.draft,
        save: acknowledged ? "clean" : "dirty",
        busy: !acknowledged,
      });
      if (acknowledged) {
        this.#clearPersisted();
        this.#recovered = null;
        return;
      }
    }
  }

  /** A 409 means the file moved and re-sending cannot help; anything else leaves the outcome unknown. */
  #failureState(error: unknown): DiagramSaveState {
    return error instanceof GroveProtocolError && error.status === 409 ? "conflict" : "error";
  }

  #key(sessionId: string): string {
    return `grove-diagram-draft:${this.workspaceId}:${sessionId}`;
  }

  #persist(xml: string): void {
    const { descriptor, baseline } = this.#snapshot;
    if (!descriptor || !baseline) return;
    this.store.write(
      this.#key(descriptor.session_id),
      JSON.stringify({ revision: baseline.revision, xml }),
    );
  }

  #clearPersisted(): void {
    const descriptor = this.#snapshot.descriptor;
    if (descriptor) this.store.clear(this.#key(descriptor.session_id));
  }

  #set(patch: Partial<DiagramSnapshot>): void {
    const next = { ...this.#snapshot, ...patch };
    const unchanged = (Object.keys(next) as (keyof DiagramSnapshot)[]).every(
      (key) => next[key] === this.#snapshot[key],
    );
    if (unchanged) return;
    this.#snapshot = next;
    for (const listener of this.#listeners) listener();
  }
}

function parseRecovered(stored: string): { revision: string; xml: string } | null {
  try {
    const value = JSON.parse(stored) as { revision?: unknown; xml?: unknown };
    return typeof value.revision === "string" && typeof value.xml === "string"
      ? { revision: value.revision, xml: value.xml }
      : null;
  } catch {
    return null;
  }
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** One collaboration per workspace, outliving every mount of the tab. */
const collaborations = new Map<string, DiagramCollaboration>();

export function diagramCollaboration(
  workspaceId: string,
  writer: DiagramWriter,
  store: DraftStore = sessionDraftStore(),
): DiagramCollaboration {
  const existing = collaborations.get(workspaceId);
  if (existing) return existing;
  const created = new DiagramCollaboration(workspaceId, writer, store);
  collaborations.set(workspaceId, created);
  return created;
}

export function resetDiagramCollaborations(): void {
  collaborations.clear();
}
