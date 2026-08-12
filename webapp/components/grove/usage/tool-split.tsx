"use client";

import { ServerIcon } from "lucide-react";

import { CardScroll } from "@/components/grove/card";
import { duration, NOT_MEASURED } from "@/components/grove/duration";
import { HEAD_CELL, LABEL_CELL, LABEL_COL, NUMERIC } from "@/components/grove/table-columns";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { UsageBreakdownRowView, UsageBreakdownView } from "@/lib/grove/api";
import { AbbreviatedNumber } from "./abbreviated-number";
import { percent } from "./format";
import { UsageSection } from "./section";

/**
 * The one thing that separates a tool a user CONFIGURED from one the harness
 * ships: an MCP tool's name is namespaced by its server.
 *
 * This is why the split needs no route, no contract field and no second
 * aggregation — it is a property of the breakdown row's `key`, so it is decided
 * where the rows are rendered.
 */
const MCP_PREFIX = "mcp__";

/** One side of the split, plus what it could NOT account for. */
type ToolClassSplit = {
  tools: number;
  calls: number;
  /** Summed `active_ms` over the rows that were TIMED. `null` when none were —
   * never `0`, which would claim the tools ran instantly. */
  ms: number | null;
  /** Rows carrying `active_ms === null`: measured calls, unmeasured time. */
  untimedTools: number;
  untimedCalls: number;
};

export type ToolSplit = {
  builtin: ToolClassSplit;
  mcp: ToolClassSplit;
  /** Every MCP row, longest first — the answer to "which server is expensive". */
  mcpRows: readonly UsageBreakdownRowView[];
  /** The denominator the shares are taken against: measured time only. */
  timedMs: number | null;
  /** The tool whose average call is longest, whichever side it falls on. */
  slowest: UsageBreakdownRowView | null;
};

/**
 * Fold the per-tool breakdown into built-in versus MCP.
 *
 * A row with `active_ms === null` was never timed, so it is EXCLUDED from the
 * time sums and counted separately instead. Folding it in as a zero would put a
 * fabricated number inside a figure presented as a total — the same lie as
 * rendering an unmeasured value as `0` (design-system §11) — and it would do it
 * invisibly, because a zero addend leaves no trace in the result. Its CALLS are
 * still counted: the call split is complete over the rows we were given, and the
 * time split is not, which is exactly the difference the card has to state.
 *
 * Exported so the arithmetic is testable without a DOM.
 */
export function toolSplit(rows: readonly UsageBreakdownRowView[]): ToolSplit {
  const mcpRows = rows
    .filter((row) => row.key.startsWith(MCP_PREFIX))
    .sort((a, b) => (b.active_ms ?? 0) - (a.active_ms ?? 0));
  const builtinRows = rows.filter((row) => !row.key.startsWith(MCP_PREFIX));

  const builtin = fold(builtinRows);
  const mcp = fold(mcpRows);
  const timedMs =
    builtin.ms === null && mcp.ms === null ? null : (builtin.ms ?? 0) + (mcp.ms ?? 0);

  return { builtin, mcp, mcpRows, timedMs, slowest: slowestPerCall(rows) };
}

function fold(rows: readonly UsageBreakdownRowView[]): ToolClassSplit {
  const timed = rows.filter((row) => typeof row.active_ms === "number");
  return {
    tools: rows.length,
    calls: rows.reduce((total, row) => total + row.tool_calls, 0),
    ms: timed.length > 0 ? timed.reduce((total, row) => total + (row.active_ms ?? 0), 0) : null,
    untimedTools: rows.length - timed.length,
    untimedCalls: rows
      .filter((row) => typeof row.active_ms !== "number")
      .reduce((total, row) => total + row.tool_calls, 0),
  };
}

/**
 * The longest average call in the set — the card's one defence against its own
 * denominator.
 *
 * `active_ms` is a wall clock, so a tool that blocks on a person is charged for
 * the wait: on this host `AskUserQuestion` reports ~16 minutes per call across
 * 168 calls, which is 9% of built-in time spent with nothing running. A reader
 * comparing "MCP versus built-in cost" assumes both sides are work, so the
 * outlier is named rather than left inside the total.
 *
 * It is DERIVED, never a list of tool names: a hard-coded exception here would
 * be this app deciding what one harness's tools mean, and it would go stale the
 * first time a harness renamed one. The call count rides alongside so a
 * one-call fluke is visible as one.
 */
function slowestPerCall(rows: readonly UsageBreakdownRowView[]): UsageBreakdownRowView | null {
  let best: UsageBreakdownRowView | null = null;
  for (const row of rows) {
    if (typeof row.active_ms !== "number" || row.tool_calls <= 0) continue;
    if (best === null || row.active_ms / row.tool_calls > (best.active_ms ?? 0) / best.tool_calls) {
      best = row;
    }
  }
  return best;
}

/**
 * How much of the fleet's tool time goes to servers the user configured.
 *
 * Reads the SAME `dimension=tool` breakdown the daemon already serves — the
 * classification is one prefix test on the row key, so a second endpoint would
 * be a second aggregation of identical rows.
 *
 * Two things this card owes the reader, both because the answer is a
 * percentage and a percentage hides its own denominator:
 *  1. the row set is capped at the daemon's `max_breakdown_rows`, so a share
 *     computed over it is a share of the tools that made the cut;
 *  2. an untimed tool contributes calls but no time, so the two columns are
 *     complete over different populations.
 */
export function UsageToolSplit({
  breakdown,
  failed,
  onRetry,
  retrying,
  className,
}: {
  breakdown: UsageBreakdownView | undefined;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}): React.ReactNode {
  const rows = breakdown?.rows ?? [];
  const split = toolSplit(rows);
  const untimedTools = split.builtin.untimedTools + split.mcp.untimedTools;
  const untimedCalls = split.builtin.untimedCalls + split.mcp.untimedCalls;

  return (
    <UsageSection
      icon={<ServerIcon />}
      title="Built-in vs MCP tools"
      description={rows.length > 0 ? `${rows.length} tools` : undefined}
      failed={failed}
      onRetry={onRetry}
      retrying={retrying}
      failure="The tool breakdown could not be loaded."
      loading={!breakdown}
      className={className}
      data-testid="usage-tool-split"
    >
      {rows.length > 0 ? (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className={`${LABEL_COL} ${HEAD_CELL}`}>Source</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Tools</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Calls</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Time</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Share</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <SplitRow label="Built-in" side={split.builtin} timedMs={split.timedMs} />
              <SplitRow label="MCP servers" side={split.mcp} timedMs={split.timedMs} />
            </TableBody>
          </Table>

          {split.mcpRows.length > 0 ? (
            <>
              {/* The second table's own header names its columns but not its
                  population, and "which server is expensive" is the question
                  the card was asked. */}
              <p className="text-xs text-content-tertiary">MCP tools by time</p>
              <CardScroll>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className={`${LABEL_COL} ${HEAD_CELL}`}>Tool</TableHead>
                      <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Calls</TableHead>
                      <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Time</TableHead>
                      <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Avg</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {split.mcpRows.map((row) => (
                      <TableRow key={row.key}>
                        {/* Mono: a tool name is a literal you would retype into
                            a config, not a quantity (design-system §3). */}
                        <TableCell className={`${LABEL_CELL} font-mono`} title={row.key}>
                          {row.key}
                        </TableCell>
                        <TableCell className={NUMERIC}>
                          <AbbreviatedNumber value={row.tool_calls} />
                        </TableCell>
                        <TableCell className={NUMERIC}>{time(row.active_ms)}</TableCell>
                        <TableCell className={NUMERIC}>{time(perCall(row))}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardScroll>
            </>
          ) : (
            <p className="text-xs text-content-tertiary">
              No MCP tool calls in this selection.
            </p>
          )}

          {split.slowest ? (
            <p className="text-xs text-content-tertiary">
              Longest average call: <span className="font-mono">{split.slowest.key}</span> at{" "}
              {duration(perCall(split.slowest))} across{" "}
              <AbbreviatedNumber value={split.slowest.tool_calls} /> calls. A tool&apos;s time is
              wall clock, so time spent waiting — on a person, or on a long process — counts
              toward whichever side it falls on.
            </p>
          ) : null}

          {untimedTools > 0 ? (
            <p className="text-xs text-content-tertiary">
              {untimedTools} tool{untimedTools === 1 ? "" : "s"} (
              <AbbreviatedNumber value={untimedCalls} /> call{untimedCalls === 1 ? "" : "s"}) were
              never timed. Their calls are counted above; their time is not, and is not counted as
              zero.
            </p>
          ) : null}

          {breakdown?.truncated ? (
            <p className="text-xs text-content-tertiary">
              The long tail was omitted at the daemon&apos;s row limit, so these shares are over{" "}
              {rows.length} tools rather than every tool that ran.
            </p>
          ) : null}
        </>
      ) : (
        <p className="text-sm text-content-tertiary">
          Not measured: no tool rows for this selection.
        </p>
      )}
    </UsageSection>
  );
}

/** One side of the split. `timedMs` is the shared denominator, so the two rows
 * cannot disagree about what 100% means. */
function SplitRow({
  label,
  side,
  timedMs,
}: {
  label: string;
  side: ToolClassSplit;
  timedMs: number | null;
}): React.ReactNode {
  return (
    <TableRow>
      <TableCell className={LABEL_CELL}>{label}</TableCell>
      <TableCell className={NUMERIC}>
        <AbbreviatedNumber value={side.tools} />
      </TableCell>
      <TableCell className={NUMERIC}>
        <AbbreviatedNumber value={side.calls} />
      </TableCell>
      <TableCell className={NUMERIC}>{time(side.ms)}</TableCell>
      <TableCell className={NUMERIC}>
        {side.ms !== null && timedMs !== null && timedMs > 0 ? (
          percent((side.ms / timedMs) * 100)
        ) : (
          <span className="text-content-tertiary">{NOT_MEASURED}</span>
        )}
      </TableCell>
    </TableRow>
  );
}

/** A duration cell that keeps an absence quiet and neutral (design-system §11).
 * `duration()` already returns the words; what it cannot know is the tier. */
function time(ms: number | null | undefined): React.ReactNode {
  return typeof ms !== "number" ? (
    <span className="text-content-tertiary">{NOT_MEASURED}</span>
  ) : (
    duration(ms)
  );
}

/** A row's average call, or `null` where the row was never timed. */
function perCall(row: UsageBreakdownRowView): number | null {
  return typeof row.active_ms === "number" && row.tool_calls > 0
    ? row.active_ms / row.tool_calls
    : null;
}
