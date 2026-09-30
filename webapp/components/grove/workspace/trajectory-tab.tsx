"use client";

import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  BellIcon,
  BotIcon,
  ChartGanttIcon,
  ChevronRightIcon,
  CircleCheckIcon,
  CircleSlashIcon,
  CircleXIcon,
  LoaderCircleIcon,
  MessageSquareTextIcon,
  UserRoundIcon,
  type LucideIcon,
} from "lucide-react";
import { AuiConfig, AuiProvider, useAui, useAuiState } from "@assistant-ui/react";
import { SpanPrimitive, SpanResource } from "@assistant-ui/react-o11y";

import { AppIcon } from "@/components/grove/app-icon";
import { ErrorState } from "@/components/elements/error-state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  buildTrajectory,
  formatSpanDuration,
  timelineTicks,
  type SpanDetail,
  type SpanStatus,
  type Trajectory,
} from "@/lib/grove/adapters/trajectory";
import { useChildTurns, useSessionTurns, useSubagentFleet } from "@/lib/grove/hooks";
import { useToolIcons } from "@/lib/grove/tool-icons";
import type { ActivityRead } from "./selectors";

/**
 * The session's whole run on one time axis: every turn, every tool call, and
 * every sub-agent with its own calls, as a collapsible Gantt waterfall.
 *
 * Composed from `@assistant-ui/react-o11y` rather than drawn: `SpanResource`
 * owns the tree, depth and the visible flat list, `SpanPrimitive.*` lay out
 * each row, and `TimelineBar` positions each bar. Grove supplies the spans
 * (`adapters/trajectory.ts`) and the look, which lives in `globals.css` under
 * `.trajectory` and keys on the primitives' own `data-span-*` attributes.
 *
 * LIVE WITHOUT A NEW STREAM. The spans are a projection of the same turns
 * query the transcript reads and the same fleet cache its card reads, so the
 * existing invalidation edges already grow this tree; a running bar extends on
 * a one-second clock that runs only while something is running.
 */
export function TrajectoryTab({
  workspaceId,
  activity,
}: {
  workspaceId: string;
  activity: ActivityRead | null;
}) {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-background" data-testid="trajectory-tab">
      <TrajectoryBody workspaceId={workspaceId} activity={activity} />
    </div>
  );
}

function TrajectoryBody({
  workspaceId,
  activity,
}: {
  workspaceId: string;
  activity: ActivityRead | null;
}) {
  const root = activity?.sessions[0] ?? null;
  const sessionId = root?.session.session_id ?? null;
  const turns = useSessionTurns(workspaceId, sessionId);
  const fleet = useSubagentFleet(sessionId === null ? null : workspaceId, sessionId);
  const serverIcons = useToolIcons();

  const children = useMemo(
    () =>
      (fleet.data?.sessions ?? []).map((entry) => {
        const a = entry.activity;
        return {
          sessionId: entry.session.session_id,
          fingerprint: `${a.state}:${a.assistant_replies}:${a.tool_calls}:${a.last_event_at ?? ""}`,
        };
      }),
    [fleet.data],
  );
  const childTurns = useChildTurns(sessionId === null ? null : workspaceId, children);

  const data = turns.query.data;
  const rootState = root?.activity.state ?? null;
  const trajectory = useMemo(
    () =>
      data && sessionId
        ? buildTrajectory({
            sessionId,
            turns: data.turns,
            firstTurnIndex: data.first_turn_index,
            rootState,
            fleet: fleet.data ?? null,
            childTurns,
            serverIcons,
          })
        : null,
    [data, sessionId, rootState, fleet.data, childTurns, serverIcons],
  );

  if (sessionId === null) {
    return (
      <Quiet
        title="No agent session yet"
        detail="The trajectory appears once this workspace's agent has started a session."
      />
    );
  }
  if (turns.query.isPending) return <TrajectorySkeleton />;
  if (turns.query.isError) {
    return (
      <ErrorState
        className="m-4"
        title="Couldn’t load this trajectory"
        detail={turns.query.error.message}
        retrying={turns.query.isFetching}
        onRetry={() => void turns.query.refetch()}
      />
    );
  }
  if (!trajectory || trajectory.spans.length === 0) {
    return (
      <Quiet
        title="Nothing to trace yet"
        detail={
          trajectory && trajectory.untimed > 0
            ? "This session's turns carry no timestamps, so they cannot be placed on a time axis."
            : "Turns, tool calls and sub-agents appear here as the agent works."
        }
      />
    );
  }

  return (
    <>
      <TrajectoryBar
        trajectory={trajectory}
        hasEarlier={turns.hasEarlier}
        loadingEarlier={turns.loadingEarlier}
        onLoadEarlier={turns.loadEarlier}
      />
      <Waterfall trajectory={trajectory} />
    </>
  );
}

const STATUS: Record<SpanStatus, { label: string; Icon: LucideIcon }> = {
  running: { label: "running", Icon: LoaderCircleIcon },
  failed: { label: "failed", Icon: CircleXIcon },
  skipped: { label: "skipped", Icon: CircleSlashIcon },
  completed: { label: "completed", Icon: CircleCheckIcon },
};

/** Read order for the summary: what needs a look first, what is settled last. */
const STATUS_ORDER: readonly SpanStatus[] = ["running", "failed", "skipped", "completed"];

/** The at-a-glance line: each status as glyph, figure and word, never hue alone. */
function TrajectoryBar({
  trajectory,
  hasEarlier,
  loadingEarlier,
  onLoadEarlier,
}: {
  trajectory: Trajectory;
  hasEarlier: boolean;
  loadingEarlier: boolean;
  onLoadEarlier: () => void;
}) {
  const turns = trajectory.spans.filter((s) => s.type === "turn").length;
  return (
    <div className="flex h-[32px] shrink-0 items-center gap-3 border-b border-border bg-muted/40 px-3 text-xs">
      <span className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden" data-testid="trajectory-summary">
        <span className="shrink-0 text-content-secondary tabular-nums">
          {turns} {turns === 1 ? "turn" : "turns"}
        </span>
        {STATUS_ORDER.filter((status) => trajectory.counts[status] > 0).map((status) => {
          const { label, Icon } = STATUS[status];
          return (
            <span
              key={status}
              className="trajectory-status inline-flex shrink-0 items-center gap-1 tabular-nums"
              data-span-status={status}
            >
              <Icon aria-hidden className="size-3.5 shrink-0" />
              <span>
                {trajectory.counts[status]} {label}
              </span>
            </span>
          );
        })}
      </span>
      {hasEarlier && (
        <Button
          variant="ghost"
          size="xs"
          className="shrink-0"
          disabled={loadingEarlier}
          onClick={onLoadEarlier}
          data-testid="trajectory-load-earlier"
        >
          {loadingEarlier ? "Loading…" : "Load earlier turns"}
        </Button>
      )}
    </div>
  );
}

const DetailsContext = createContext<ReadonlyMap<string, SpanDetail>>(new Map());

/**
 * Ids already collapsed once by default. A ref, not state: it only records
 * that the default was applied, so a subtree the reader opened stays open as
 * the tree streams, and a span that gains its first child later still starts
 * closed.
 */
const CollapsedOnceContext = createContext<Set<string>>(new Set());

function Waterfall({ trajectory }: { trajectory: Trajectory }) {
  const collapsedOnce = useRef(new Set<string>()).current;
  const running = trajectory.counts.running > 0 || trajectory.spans.some((s) => s.endedAt === null);
  const now = useRunningClock(running);
  const range = useMemo(() => {
    let min = Infinity;
    let max = -Infinity;
    for (const s of trajectory.spans) {
      min = Math.min(min, s.startedAt);
      max = Math.max(max, s.endedAt ?? s.startedAt);
    }
    if (running && now !== null) max = Math.max(max, now);
    return { min, max: Math.max(max, min + 1000) };
  }, [trajectory.spans, running, now]);
  const ticks = useMemo(() => timelineTicks(range), [range]);
  const total = formatSpanDuration(range.max - range.min);

  return (
    <AuiProvider config={AuiConfig({ span: SpanResource({ spans: trajectory.spans }) })}>
      <DetailsContext.Provider value={trajectory.details}>
        <CollapsedOnceContext.Provider value={collapsedOnce}>
          <div className="trajectory min-h-0 min-w-0 flex-1 overflow-y-auto" data-testid="trajectory-waterfall">
            <div className="trajectory-grid trajectory-axis sticky top-0 z-10 border-b border-border bg-background">
              <span className="flex items-center justify-between gap-2 px-3 text-xs text-content-tertiary">
                <span>Step</span>
                <span className="tabular-nums" title="Wall-clock span of the loaded turns">
                  {total}
                </span>
              </span>
              <span className="relative" aria-hidden>
                {ticks.map((tick) => (
                  <span
                    key={tick.percent}
                    className="trajectory-tick absolute inset-y-0 flex items-center ps-1 text-xs text-content-tertiary tabular-nums"
                    style={{ insetInlineStart: `${tick.percent}%` }}
                  >
                    {tick.label}
                  </span>
                ))}
              </span>
            </div>
            <SpanPrimitive.Timeline timeRange={range} className="relative">
              <span className="trajectory-gridlines trajectory-grid" aria-hidden>
                <span>
                  {ticks.map((tick) => (
                    <span key={tick.percent} style={{ insetInlineStart: `${tick.percent}%` }} />
                  ))}
                </span>
              </span>
              <NowContext.Provider value={running ? now : null}>
                <SpanPrimitive.Children components={SPAN_COMPONENTS} />
              </NowContext.Provider>
            </SpanPrimitive.Timeline>
          </div>
        </CollapsedOnceContext.Provider>
      </DetailsContext.Provider>
    </AuiProvider>
  );
}

const NowContext = createContext<number | null>(null);

/**
 * One row: the label column (indent, disclosure, status, mark, name,
 * duration) beside the bar's lane.
 *
 * `SpanResource` has no initial collapsed set and no way to collapse a span
 * from outside its own scope, so the default is applied HERE, once per span,
 * in a layout effect: the flat list is rebuilt before the browser paints, so a
 * reader never sees a subtree that is about to close.
 */
function SpanRow() {
  const aui = useAui();
  const id = useAuiState((s) => s.span.id);
  const name = useAuiState((s) => s.span.name);
  const status = useAuiState((s) => s.span.status);
  const latency = useAuiState((s) => s.span.latencyMs);
  const hasChildren = useAuiState((s) => s.span.hasChildren);
  const detail = useContext(DetailsContext).get(id);
  const collapsedOnce = useContext(CollapsedOnceContext);
  const now = useContext(NowContext);

  useLayoutEffect(() => {
    if (!hasChildren || collapsedOnce.has(id)) return;
    collapsedOnce.add(id);
    aui.span.toggleCollapse();
  }, [aui, collapsedOnce, hasChildren, id]);

  const { label, Icon } = STATUS[status];
  const duration = formatSpanDuration(latency);
  return (
    <SpanPrimitive.Root className="trajectory-row trajectory-grid" data-testid="trajectory-span">
      <SpanPrimitive.Indent baseIndent={4} indentPerLevel={14} className="flex min-w-0 items-center gap-1.5 pe-3">
        {hasChildren ? (
          <SpanPrimitive.CollapseToggle
            className="trajectory-toggle flex size-5 shrink-0 items-center justify-center text-content-tertiary"
            aria-label={`Show or hide the steps under ${name}`}
          >
            <ChevronRightIcon aria-hidden className="size-3.5" />
          </SpanPrimitive.CollapseToggle>
        ) : (
          <span className="size-5 shrink-0" />
        )}
        <SpanPrimitive.StatusIndicator className="trajectory-status flex shrink-0" title={label}>
          <Icon aria-hidden className="size-3.5" />
          <span className="sr-only">{label}</span>
        </SpanPrimitive.StatusIndicator>
        <KindMark detail={detail} />
        <SpanPrimitive.Name
          className="trajectory-name min-w-0 flex-1 truncate text-sm"
          title={detail?.title ?? name}
        />
        {duration && (
          <span className="shrink-0 text-xs text-content-tertiary tabular-nums">{duration}</span>
        )}
      </SpanPrimitive.Indent>
      <span className="trajectory-lane relative">
        <SpanPrimitive.TimelineBar className="trajectory-bar" now={now ?? undefined} />
      </span>
    </SpanPrimitive.Root>
  );
}

const SPAN_COMPONENTS = { Span: SpanRow };

const KIND_GLYPH: Record<Exclude<SpanDetail["kind"], "tool">, LucideIcon> = {
  turn: UserRoundIcon,
  agent: BotIcon,
  message: MessageSquareTextIcon,
  notice: BellIcon,
};

/** A tool wears its catalog mark, the same one its transcript step shows. */
function KindMark({ detail }: { detail: SpanDetail | undefined }) {
  if (detail?.kind === "tool") return <AppIcon slug={detail.icon} className="size-4 shrink-0" />;
  const Glyph = KIND_GLYPH[detail?.kind === undefined ? "message" : detail.kind];
  return <Glyph aria-hidden className="size-4 shrink-0 text-content-tertiary" />;
}

/**
 * The instant running bars extend to, ticking once a second — and only while
 * something is running and the document is visible, so a settled trace and a
 * hidden tab cost nothing.
 */
function useRunningClock(running: boolean): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    if (!running) return undefined;
    let timer: ReturnType<typeof setInterval> | undefined;
    const sync = () => {
      clearInterval(timer);
      timer = undefined;
      if (document.hidden) return;
      setNow(Date.now());
      timer = setInterval(() => setNow(Date.now()), 1000);
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", sync);
    };
  }, [running]);
  return now;
}

function Quiet({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="p-3" data-testid="trajectory-empty">
      <Alert>
        <ChartGanttIcon aria-hidden />
        <AlertTitle>{title}</AlertTitle>
        <AlertDescription>
          <p className="text-xs text-content-tertiary">{detail}</p>
        </AlertDescription>
      </Alert>
    </div>
  );
}

function TrajectorySkeleton() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-3" aria-hidden>
      <Skeleton className="h-4 w-48" />
      {Array.from({ length: 6 }, (_, index) => (
        <Skeleton key={index} className="h-5 w-full" />
      ))}
    </div>
  );
}

