import { GroveClient } from "@/lib/grove/api";

/**
 * The one client every hook uses.
 *
 * `GroveClient` holds no state — it composes URLs against the cookie-authed BFF
 * — so a module singleton is the honest shape, and it keeps every hook's query
 * function referentially stable across renders.
 */
export const groveClient: GroveClient = GroveClient.create();

/**
 * Poll cadences, in one table.
 *
 * Most of these are BACKSTOPS, not the freshness mechanism: the event stream is
 * what makes a surface live, and `backstopInterval` in `stream.tsx` switches
 * the entry off while the stream is connected. Read those as "how stale may
 * this get with no stream at all".
 *
 * The entries marked UNGATED describe data no frame on `/events` carries, so
 * they run whatever the stream is doing. Each says why at its `queries.ts` call
 * site — the reason belongs beside the decision, not in this table.
 */
export const POLL_MS = {
  /** Working-tree stats behind an open workspace. */
  peek: 2_000,
  /** An open workspace's steer queue. The stream invalidates on the depth edge. */
  queue: 15_000,
  fleet: 15_000,
  /**
   * The agent's current plan behind an open workspace.
   *
   * Matches `queue` rather than `turns` because it is the same shape of read: a
   * small fetch-on-demand route whose content no event carries. It exists at
   * all only because the transcript is now windowed to its tail — the plan used
   * to be derived from loaded turns, which is correct only while the client
   * holds the session from turn zero.
   */
  todo: 15_000,
  /** UNGATED. A container build's progress; only mounted while one is running. */
  provision: 2_000,
  /** Commits behind an open workspace. The stream invalidates on the edge. */
  commits: 15_000,
  /** UNGATED. A workspace's session list. */
  sessions: 15_000,
  /** An open transcript. The stream fingerprint is what actually refreshes it. */
  turns: 30_000,
  /** UNGATED. The host-wide session catalog — a browse surface, off the tick. */
  catalog: 30_000,
  /**
   * The working-tree patch, and the largest payload here by far.
   *
   * GATED since `workspace_source_changed`: `dirty_files` moves only when the
   * SET changes, so editing an already-dirty file carried no delta and this
   * interval was the only freshness there was. That event fires per filesystem
   * invalidation regardless of whether a counter moved, so the edge now covers
   * the case, and this is the stream-drop backstop.
   */
  diff: 30_000,
  /**
   * UNGATED, and mounted only by a VISIBLE, EDITABLE diagram.
   *
   * No frame on `/events` carries file bytes, so an interval is the only way to
   * notice that an agent or a shell wrote the `.drawio` under an open editor —
   * and noticing is the whole point, since it is what turns a silent overwrite
   * into a visible conflict. Deliberately not gated on window focus: a second
   * monitor showing the diagram while an agent edits it is exactly the case
   * this serves. It IS gated on the tab being on screen and the collaboration
   * being active, so a stopped or unread diagram costs nothing.
   */
  diagram: 5_000,
} as const;
