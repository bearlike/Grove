import type { SpanData } from "@assistant-ui/react-o11y";

import type {
  DigestEntryView,
  SessionTurnView,
  SubagentFleetView,
} from "@/lib/grove/api";
import { compactToolTarget, toolPresentation } from "./tool-catalog";

/**
 * A session's transcript and child fleet, projected onto `react-o11y` spans.
 *
 * Pure, like every adapter: the wire already carries every fact a trace needs —
 * each entry's clock (`at`), each call's `duration_ms` and settled status, and
 * a sub-agent's spawning `tool_use_id` — so this is a reshaping, never an
 * inference about what the agent meant.
 *
 * The tree is turn → step → sub-agent → step. A turn is a root so the collapsed
 * default still reads as the whole run at a glance; a sub-agent nests under the
 * exact call that spawned it where the provider recorded that link, and under
 * the turn it started in where it did not.
 */

export type SpanStatus = SpanData["status"];

/** What a span IS — drives the row's mark and the bar's weight, never its hue. */
export type TrajectoryKind = "turn" | "tool" | "message" | "notice" | "agent";

/** The agent axis's states, as far as a trace reads them. */
type AgentStateLike = string;

/** Row presentation the span shape has no field for. */
export interface SpanDetail {
  kind: TrajectoryKind;
  /** An Iconify slug for a tool's catalog mark; `null` takes the kind's glyph. */
  icon: string | null;
  /** The untruncated label, for the row's hover. */
  title: string;
}

export interface Trajectory {
  spans: SpanData[];
  details: ReadonlyMap<string, SpanDetail>;
  /** Every step's status, turns excluded — the at-a-glance line. */
  counts: Record<SpanStatus, number>;
  /** Turns the provider stamped no clock on, so they cannot sit on an axis. */
  untimed: number;
}

export interface TrajectoryInput {
  sessionId: string;
  turns: readonly SessionTurnView[];
  /** The ordinal of `turns[0]`, so a span id survives a window widening. */
  firstTurnIndex: number;
  /** The root session's agent state; `null` while the snapshot has not said. */
  rootState: AgentStateLike | null;
  fleet: SubagentFleetView | null;
  /** Each child session's turns, keyed by its session id; absent while loading. */
  childTurns: ReadonlyMap<string, readonly SessionTurnView[]>;
  serverIcons?: Readonly<Record<string, string>>;
}

/** A root in one of these has closed its turn: an unresolved call will never return. */
const SETTLED: ReadonlySet<string> = new Set(["idle", "waiting", "error"]);
/** A root in one of these is positively mid-run, so its last turn is too. */
const ACTIVE: ReadonlySet<string> = new Set(["working", "starting", "blocked"]);

const LABEL_LIMIT = 120;

export function buildTrajectory(input: TrajectoryInput): Trajectory {
  const { sessionId, turns, firstTurnIndex, rootState, fleet, childTurns } = input;
  const icons = input.serverIcons ?? {};
  const spans: SpanData[] = [];
  const details = new Map<string, SpanDetail>();
  const turnStarts: { id: string; start: number }[] = [];
  let untimed = 0;

  const rootSettled = rootState !== null && SETTLED.has(rootState);
  const rootActive = rootState !== null && ACTIVE.has(rootState);

  turns.forEach((turn, offset) => {
    const isLast = offset === turns.length - 1;
    const start = instant(turn.started_at) ?? firstInstant(turn.entries);
    if (start === null) {
      untimed += 1;
      return;
    }
    const id = `t:${firstTurnIndex + offset}`;
    // Only the tail turn can still be running; an unresolved call anywhere
    // earlier was abandoned when the next prompt arrived.
    const live = isLast && !rootSettled;
    const steps = stepSpans(sessionId, turn.entries, id, start, live, icons, `${firstTurnIndex + offset}`);
    const running = isLast && rootActive;
    const end = running ? null : latestEnd(start, steps);
    spans.push(span(id, null, turnLabel(turn.user_text), "turn", running ? "running" : "completed", start, end));
    details.set(id, { kind: "turn", icon: null, title: turn.user_text.trim() || "Continued session" });
    turnStarts.push({ id, start });
    for (const step of steps) {
      spans.push(step.span);
      details.set(step.span.id, step.detail);
    }
  });

  const placed = new Set(spans.map((s) => s.id));
  // A child that started before the held window has no turn here to sit
  // under, so it waits for "load earlier" rather than joining the wrong one.
  const turnAt = (at: number): string | null => {
    let owner: string | null = null;
    for (const turn of turnStarts) if (turn.start <= at) owner = turn.id;
    return owner;
  };
  const startOf = (id: string): number => spans.find((s) => s.id === id)?.startedAt ?? 0;

  for (const entry of fleet?.sessions ?? []) {
    const childId = entry.session.session_id;
    const id = `a:${childId}`;
    const spawn = entry.session.spawn_tool_use_id;
    const spawnSpan = spawn ? `c:${sessionId}:${spawn}` : null;
    const reported = instant(entry.activity.started_at ?? null);
    const parent = spawnSpan && placed.has(spawnSpan) ? spawnSpan : reported !== null ? turnAt(reported) : null;
    if (parent === null) continue;
    const start = reported ?? startOf(parent);
    const status = childStatus(entry.activity.state, rootSettled);
    const title = entry.activity.current_task ?? entry.activity.title ?? childId;
    const running = status === "running";
    const children = childTurns.get(childId) ?? [];
    const steps = children.flatMap((turn, index) =>
      stepSpans(childId, turn.entries, id, instant(turn.started_at) ?? start, running, icons, `${index}`),
    );
    const settledAt = instant(entry.activity.last_event_at ?? null);
    const end = running ? null : Math.max(settledAt ?? start, latestEnd(start, steps));
    spans.push(span(id, parent, firstLine(title), "agent", status, start, end));
    details.set(id, { kind: "agent", icon: null, title });
    placed.add(id);
    for (const step of steps) {
      spans.push(step.span);
      details.set(step.span.id, step.detail);
    }
  }

  // A hook-only child has no transcript yet: a bar and a state, no steps.
  for (const member of fleet?.subagents ?? []) {
    const id = `a:${member.agent_id}`;
    if (placed.has(id)) continue;
    const start = instant(member.started_at);
    const parent = start === null ? null : turnAt(start);
    if (start === null || parent === null) continue;
    const status = childStatus(member.state, rootSettled);
    const end = status === "running" ? null : (instant(member.last_event_at) ?? start);
    const title = member.agent_type ?? member.agent_id;
    spans.push(span(id, parent, title, "agent", status, start, end));
    details.set(id, { kind: "agent", icon: null, title });
    placed.add(id);
  }

  const counts: Record<SpanStatus, number> = { running: 0, completed: 0, failed: 0, skipped: 0 };
  for (const s of spans) if (s.type !== "turn") counts[s.status] += 1;
  return { spans, details, counts, untimed };
}

interface Step {
  span: SpanData;
  detail: SpanDetail;
}

/**
 * One turn's entries as spans under `parent`.
 *
 * An entry the provider stamped no clock on inherits its predecessor's, so it
 * keeps its ORDER on the axis without claiming a time of its own. Several
 * entries can share one call (a batch edit fans out); the call is one span.
 */
function stepSpans(
  sessionId: string,
  entries: readonly DigestEntryView[],
  parent: string,
  turnStart: number,
  live: boolean,
  icons: Readonly<Record<string, string>>,
  scope: string,
): Step[] {
  const steps: Step[] = [];
  const calls = new Set<string>();
  const abandoned: SpanData[] = [];
  let clock = turnStart;
  entries.forEach((entry, index) => {
    clock = instant(entry.at ?? null) ?? clock;
    const call = entry.tool;
    if (call) {
      const key = call.tool_use_id || `${scope}.${index}`;
      if (calls.has(key)) return;
      calls.add(key);
      const step = toolPresentation(call.name, call.input ?? null, entry.text, icons);
      const target = compactToolTarget(step);
      const label = target && target !== call.name ? `${step.verb} ${target}` : `${step.verb} ${call.name}`;
      const status: SpanStatus =
        call.status === "error" ? "failed"
        : call.status === "ok" ? "completed"
        : live ? "running" : "skipped";
      const end =
        status === "running" || status === "skipped" ? null
        : clock + Math.max(0, call.duration_ms ?? 0);
      const s = span(`c:${sessionId}:${key}`, parent, label, "tool", status, clock, end);
      if (status === "skipped") abandoned.push(s);
      steps.push({ span: s, detail: { kind: "tool", icon: step.icon, title: `${step.verb} ${step.chip}` } });
      return;
    }
    const kind: TrajectoryKind | null =
      entry.role === "assistant" ? "message"
      : entry.role === "notification" || entry.role === "compaction" ? "notice"
      : null;
    if (kind === null || !entry.text.trim()) return;
    const id = `${kind === "message" ? "m" : "n"}:${sessionId}:${scope}.${index}`;
    steps.push({
      span: span(id, parent, firstLine(entry.text), kind, "completed", clock, clock),
      detail: { kind, icon: null, title: entry.text.trim() },
    });
  });
  // A call that never returned ran until its turn stopped, not for zero time.
  const stop = latestEnd(turnStart, steps);
  for (const s of abandoned) {
    s.endedAt = Math.max(stop, s.startedAt);
    s.latencyMs = s.endedAt - s.startedAt;
  }
  return steps;
}

/**
 * A child's run, read against its root. A child killed mid-tool keeps claiming
 * `working`, so once the root has closed its turn that claim is a stopped run —
 * the fleet card's own rule, shown here as `skipped` rather than as a failure.
 */
function childStatus(state: string, rootSettled: boolean): SpanStatus {
  if (state === "error") return "failed";
  if (ACTIVE.has(state)) return rootSettled ? "skipped" : "running";
  return "completed";
}

function span(
  id: string,
  parentSpanId: string | null,
  name: string,
  type: TrajectoryKind,
  status: SpanStatus,
  startedAt: number,
  endedAt: number | null,
): SpanData {
  return {
    id,
    parentSpanId,
    name,
    type,
    status,
    startedAt,
    endedAt,
    latencyMs: endedAt === null ? null : Math.max(0, endedAt - startedAt),
  };
}

function latestEnd(start: number, steps: readonly Step[]): number {
  let end = start;
  for (const { span: s } of steps) end = Math.max(end, s.endedAt ?? s.startedAt);
  return end;
}

function firstInstant(entries: readonly DigestEntryView[]): number | null {
  for (const entry of entries) {
    const at = instant(entry.at ?? null);
    if (at !== null) return at;
  }
  return null;
}

function instant(iso: string | null): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

function turnLabel(prompt: string): string {
  return firstLine(prompt) || "Continued session";
}

function firstLine(text: string): string {
  const line = text.trim().split("\n", 1)[0]?.trim() ?? "";
  return line.length > LABEL_LIMIT ? `${line.slice(0, LABEL_LIMIT - 1).trimEnd()}…` : line;
}

/**
 * A span's length in the units a reader compares: seconds under a minute,
 * minutes under an hour, hours beyond. `null` while it runs.
 */
export function formatSpanDuration(ms: number | null): string | null {
  if (ms === null) return null;
  if (ms < 1000) return ms < 1 ? "0s" : "<1s";
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

const TICK_STEPS_MS = [
  1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400,
].map((s) => s * 1000);

/**
 * Evenly spaced axis marks on a round step, at most `limit` of them past zero.
 * The step is the smallest round unit that keeps the count under the limit, so
 * the labels read `0 · 30s · 1m` rather than `0 · 27s · 54s`.
 */
export function timelineTicks(
  range: { min: number; max: number },
  limit = 5,
): { percent: number; label: string }[] {
  const span = Math.max(1, range.max - range.min);
  const step = TICK_STEPS_MS.find((candidate) => span / candidate <= limit) ?? span / limit;
  const ticks: { percent: number; label: string }[] = [];
  for (let at = 0; at <= span; at += step) {
    // An axis mark is a round instant, so a zero remainder is noise: `1m`, not `1m 0s`.
    const label = at === 0 ? "0" : (formatSpanDuration(at) ?? "").replace(/ 0[smh]$/, "");
    ticks.push({ percent: (at / span) * 100, label });
  }
  return ticks;
}
