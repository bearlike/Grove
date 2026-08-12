"use client";

import { HistoryIcon } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { UsageSessionRowView, UsageSessionPageView } from "@/lib/grove/api";
import { AbbreviatedNumber } from "./abbreviated-number";
import { CardScroll } from "@/components/grove/card";
import { Explain } from "@/components/grove/glossary";
import { UsageSection } from "./section";
import {
  CAPPED_COL,
  HEAD_CELL,
  LABEL_CELL,
  LABEL_COL,
  NUMERIC,
} from "@/components/grove/table-columns";
import { abbreviate, duration, projectLabel, timestamp, tokenTotal } from "./format";

/**
 * How much of a session's token spend went to sub-agents it delegated to, as a
 * percentage — or `null` when that share is not a number anyone measured.
 *
 * A SHARE RATHER THAN A COUNT, because the count answers nothing on its own:
 * 12M delegated tokens is a rounding error beside a 900M session and almost
 * the whole job beside a 15M one, and the total is already the column to its
 * left. The absolute figure stays one hover away rather than being destroyed.
 *
 * `null` in three genuinely different situations that all mean *do not draw a
 * number here*: no sub-agent usage was measured (`subagent_tokens` is null by
 * contract, never a fabricated zero), the session's own total was not
 * measured, or the total is zero and a share of nothing is undefined. Pure, so
 * the arithmetic is pinned by a test rather than by a render.
 */
export function delegatedPercent(row: UsageSessionRowView): number | null {
  const delegated = tokenTotal(row.subagent_tokens);
  const total = tokenTotal(row.tokens);
  if (delegated === null || total === null || total <= 0) return null;
  return (delegated / total) * 100;
}

/**
 * The most recent sessions, newest first, inside a fixed box.
 *
 * The list is a sample, not a ledger — it is capped by the query and bounded by
 * the box, because the value here is "what has been running lately", and a
 * reader who needs the full history goes to the session browser.
 */
export function UsageSessions({
  sessions,
  failed,
  onRetry,
  retrying,
  className,
}: {
  sessions: UsageSessionPageView | undefined;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}): React.ReactNode {
  const rows = sessions?.rows ?? [];

  return (
    <UsageSection
      icon={<HistoryIcon />}
      title="Recent sessions"
      description={rows.length > 0 ? `${rows.length} most recent` : undefined}
      failed={failed}
      onRetry={onRetry}
      retrying={retrying}
      failure="Sessions could not be loaded."
      loading={!sessions}
      className={className}
      data-testid="usage-sessions"
    >
      {rows.length > 0 ? (
        <CardScroll>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className={HEAD_CELL}>Session</TableHead>
                <TableHead className={`${LABEL_COL} ${HEAD_CELL}`}>Project</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Turns</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Tools</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Tokens</TableHead>
                {/* The delegated SHARE of the column to its left, not a second
                    token count: `tokens` already includes every sub-agent's
                    spend, so a reader looking at 900M cannot tell whether one
                    agent or thirty produced it. Nullable by contract and
                    rendered as such — an unmeasured share is a dash, never
                    `0%`, which would claim nothing was delegated. */}
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>
                  <Explain term="delegated_tokens">Delegated</Explain>
                </TableHead>
                {/* THREE durations, never one, and never the same three as the
                    Timeline card. `Wall clock` is the union of the session's
                    active intervals — real time with the waits for a human
                    removed, concurrent sub-agents counted once. The other two
                    are the SUM of those intervals split by what the agent was
                    waiting on: the model, or a tool. Their total is `Compute`,
                    which is deliberately NOT its own column here — it is
                    exactly `Model wait + Tool time`, so spending a fourth
                    numeric column on a number the reader can add would crowd
                    out the two that answer the question the total cannot
                    ("was this session slow because of the model or because of
                    the test suite"). The workspace Timeline card is a vertical
                    field list and has the room, so it shows all four.

                    The one-line definition behind each word is the shared
                    glossary entry (`components/grove/glossary.tsx`), same as
                    the session browser's table. */}
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>
                  <Explain term="clock_time">Wall clock</Explain>
                </TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>
                  <Explain term="model_wait">Model wait</Explain>
                </TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>
                  <Explain term="tool_time">Tool time</Explain>
                </TableHead>
                <TableHead className={`${CAPPED_COL} ${HEAD_CELL}`}>Last active</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={`${row.source_id}:${row.session_id}`}>
                  <TableCell className="font-mono text-xs" title={row.session_id}>
                    {row.session_id.slice(0, 8)}
                    <span className="ms-2 font-sans text-content-tertiary">
                      {row.account_label ?? row.provider}
                    </span>
                  </TableCell>
                  <TableCell
                    className={LABEL_CELL}
                    title={row.project ?? row.cwd ?? "unknown project"}
                  >
                    {projectLabel(row.project ?? row.cwd)}
                  </TableCell>
                  <TableCell className={NUMERIC}>
                    <AbbreviatedNumber value={row.turns} />
                  </TableCell>
                  <TableCell className={NUMERIC}>
                    <AbbreviatedNumber value={row.tool_calls} />
                  </TableCell>
                  <TableCell className={NUMERIC}>
                    <AbbreviatedNumber value={tokenTotal(row.tokens)} />
                  </TableCell>
                  <TableCell className={NUMERIC}>
                    <DelegatedShare row={row} />
                  </TableCell>
                  <TableCell className={NUMERIC}>{duration(row.duration.active_ms)}</TableCell>
                  <TableCell className={NUMERIC}>{duration(row.duration.generation_ms)}</TableCell>
                  <TableCell className={NUMERIC}>{duration(row.duration.tool_ms)}</TableCell>
                  <TableCell className="w-px whitespace-nowrap text-content-tertiary">
                    {timestamp(row.last_event_at ?? row.started_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardScroll>
      ) : (
        <p className="text-sm text-content-tertiary">
          Not measured: no session matches this selection.
        </p>
      )}
    </UsageSection>
  );
}

/**
 * The delegated share as a cell — a percentage, a dash, or `<1%`.
 *
 * NEVER `0%` for an unmeasured share, the same rule the turn count in the
 * session browser follows and the same reason: a zero here reads as "nothing
 * was delegated", which is a measurement, where the truth is that nobody took
 * one. The dash carries a title rather than standing mute, because a bare `—`
 * in a numeric column reads as a rendering failure to anyone not told.
 *
 * `<1%` rather than a rounded `0%` for a real but tiny share, so the one value
 * that WOULD round to the forbidden number keeps saying that something was
 * delegated. The exact token count rides in the title — the same bargain the
 * abbreviated token column strikes.
 */
function DelegatedShare({ row }: { row: UsageSessionRowView }): React.ReactNode {
  const percent = delegatedPercent(row);
  if (percent === null) {
    return (
      <span className="text-content-tertiary" title="No sub-agent usage measured for this session">
        —
      </span>
    );
  }
  const delegated = tokenTotal(row.subagent_tokens);
  return (
    <span className="tabular-nums" title={`${abbreviate(delegated)} tokens spent by sub-agents`}>
      {percent > 0 && percent < 1 ? "<1%" : `${Math.round(percent)}%`}
    </span>
  );
}
