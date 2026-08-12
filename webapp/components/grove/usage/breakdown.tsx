"use client";

import { CpuIcon } from "lucide-react";

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
import { CardScroll } from "@/components/grove/card";
import { duration } from "@/components/grove/duration";
import { UsageSection } from "./section";
import { HEAD_CELL, LABEL_CELL, LABEL_COL, NUMERIC } from "@/components/grove/table-columns";

/**
 * Where the work went, by model.
 *
 * Sorted by the daemon and shown whole inside a bounded box rather than
 * truncated to a top-N: twenty models is exactly the case where the long tail
 * ("something still runs on gpt-5.2") is the finding.
 */
export function UsageBreakdown({
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

  return (
    <UsageSection
      icon={<CpuIcon />}
      title="By model"
      description={rows.length > 0 ? `${rows.length} models` : undefined}
      failed={failed}
      onRetry={onRetry}
      retrying={retrying}
      failure="The model breakdown could not be loaded."
      loading={!breakdown}
      className={className}
      data-testid="usage-breakdown"
    >
      {rows.length > 0 ? (
        <CardScroll>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className={`${LABEL_COL} ${HEAD_CELL}`}>Model</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Sessions</TableHead>
                {/* The classes, NOT their fold. A single `Tokens` total sat
                    here until the classes joined it, and showing both would
                    restate one magnitude twice — once as a mystery and once
                    explained — which is the same rule the workspace Info tab
                    follows when its breakdown arrives. `In` is every input
                    class (fresh + cache read + cache write) because that is
                    what a model is charged to READ; reasoning and output are
                    what it charges to WRITE. */}
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>In</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Reasoning</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Out</TableHead>
                {/* The model's own wait, not the session's total activity time
                    — a picker choosing between models for an orchestration
                    tool cares how long a single invocation takes, which
                    volume alone cannot answer. */}
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Avg latency</TableHead>
                <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Tok/s</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.key}>
                  <TableCell className={LABEL_CELL} title={row.label}>
                    {row.label}
                  </TableCell>
                  <TableCell className={NUMERIC}>
                    <AbbreviatedNumber value={row.sessions} />
                  </TableCell>
                  <TableCell className={NUMERIC}>{figure(inputTokens(row))}</TableCell>
                  <TableCell className={NUMERIC}>
                    {figure(row.tokens?.reasoning ?? null)}
                  </TableCell>
                  <TableCell className={NUMERIC}>{figure(row.tokens?.output ?? null)}</TableCell>
                  <TableCell className={NUMERIC} title={latencyBasis(row)}>
                    {latency(row)}
                  </TableCell>
                  <TableCell className={NUMERIC} title={throughputBasis(row)}>
                    {throughput(row)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardScroll>
      ) : (
        <p className="text-sm text-content-tertiary">
          Not measured: no model rows for this selection.
        </p>
      )}
      {breakdown?.truncated ? (
        <p className="mt-2 text-xs text-content-tertiary">
          The long tail was omitted at the daemon&apos;s row limit.
        </p>
      ) : null}
    </UsageSection>
  );
}

/**
 * A token figure, or the same dash every other numeric column here uses.
 *
 * `AbbreviatedNumber` renders a null as the words "not measured", which is
 * right in a `CardField` and wrong here for the reason `latency` below already
 * states: a sentence in a right-aligned figure column out-weighs the figures
 * beside it. It also had a second cost this table could not absorb. The Model
 * column is sized as the REMAINDER (see `table-columns.ts`), so three cells
 * each holding a two-word sentence took the remainder away and ellipsised
 * every model id to `claud…` — a "By model" table that cannot name its models.
 * Reasoning is null on exactly the models a reader most wants to identify,
 * which is why the collision was total rather than occasional.
 */
function figure(value: number | null): React.ReactNode {
  return typeof value === "number" ? <AbbreviatedNumber value={value} /> : "—";
}

/**
 * The model's own average wait, or a dash when nothing measured it.
 *
 * `duration(null)` renders the words "not measured", which is right in a
 * `CardField` and wrong in a right-aligned numeric column — a sentence in a
 * figure column out-weighs the figures beside it, and §11 asks a null to drop a
 * tier rather than shout. The dash is the same absence mark every other numeric
 * column on this page already uses.
 *
 * `latency` is optional-chained deliberately even though the wire declares it
 * required-with-default: a browser holding a build newer than the daemon it is
 * talking to is the ordinary state of this app between a merge and a daemon
 * restart (the three surfaces refresh separately), and an unguarded read there
 * throws and blanks the whole page rather than degrading one cell.
 */
function latency(row: UsageBreakdownRowView): React.ReactNode {
  const ms = row.latency?.avg_ms;
  return typeof ms === "number" ? duration(ms) : "—";
}

/** What the average rests on, for the cell's title. An average over one call
 * and an average over a thousand are not the same claim, so the count travels
 * with it rather than being implied by the figure. */
function latencyBasis(row: UsageBreakdownRowView): string | undefined {
  const calls = row.latency?.calls ?? 0;
  if (calls === 0) return "No generation call here reported a measurable wait.";
  return `Averaged over ${calls.toLocaleString("en-US")} measured call${calls === 1 ? "" : "s"}.`;
}

/**
 * Every input class summed — what the model was charged to READ.
 *
 * Fresh input, cache read and cache write are three different prices for the
 * same job, and a picker comparing models wants the volume before it wants the
 * split (which the workspace Info tab already gives, per session). `null` when
 * no class was reported: `AbbreviatedNumber` renders that as "not measured",
 * never as a zero nobody measured.
 */
function inputTokens(row: UsageBreakdownRowView): number | null {
  const parts = [
    row.tokens?.fresh_input,
    row.tokens?.cache_read,
    row.tokens?.cache_creation,
  ].filter((value): value is number => typeof value === "number");
  return parts.length > 0 ? parts.reduce((total, value) => total + value, 0) : null;
}

/**
 * Output tokens per second of the model's OWN time — the throughput a reader
 * feels, derived from two figures already on the row.
 *
 * `latency.avg_ms * latency.calls` is the total generation time those calls
 * spent, so dividing output by it answers "how fast does this model write",
 * independent of how long its tools took. That division is only honest because
 * both inputs describe the same population — the daemon restricts the latency
 * read to the row's own single-model sessions for exactly this reason; before
 * that fix this number would have divided one population's tokens by another's
 * time and looked entirely plausible.
 *
 * Reasoning tokens are excluded from the numerator deliberately: not every
 * provider reports them, so including them would make the figure faster for
 * the providers that stay quiet about their reasoning. Output alone is the one
 * class every provider reports.
 *
 * `null` propagates from either input — never a fabricated 0 tok/s.
 */
function throughput(row: UsageBreakdownRowView): React.ReactNode {
  const output = row.tokens?.output;
  const avgMs = row.latency?.avg_ms;
  const calls = row.latency?.calls ?? 0;
  if (typeof output !== "number" || typeof avgMs !== "number" || calls === 0) return "—";
  const seconds = (avgMs * calls) / 1000;
  if (seconds <= 0) return "—";
  return Math.round(output / seconds).toLocaleString("en-US");
}

function throughputBasis(row: UsageBreakdownRowView): string | undefined {
  const calls = row.latency?.calls ?? 0;
  if (typeof row.tokens?.output !== "number" || calls === 0) {
    return "Needs both an output-token count and a measured generation time.";
  }
  return "Output tokens per second of generation time — tool time excluded.";
}
