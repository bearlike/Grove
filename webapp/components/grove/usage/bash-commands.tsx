"use client";

import { TerminalIcon } from "lucide-react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CardScroll } from "@/components/grove/card";
import { duration } from "@/components/grove/duration";
import { Explain } from "@/components/grove/glossary";
import {
  CAPPED_CELL,
  CAPPED_COL,
  HEAD_CELL,
  LABEL_COL,
  NUMERIC,
} from "@/components/grove/table-columns";
import type { UsageBashCommandView, UsageBashInsightView } from "@/lib/grove/api";
import { AbbreviatedNumber } from "./abbreviated-number";
import { NOT_MEASURED, exact, percent } from "./format";
import { UsageSection } from "./section";

/**
 * Which processes eat the most agent time — a 111,578-command census settled
 * what this number is allowed to claim, and the settlement has to survive
 * into the row, not just into a code comment:
 *
 *  1. This is time in Bash calls LED BY a command, never time SPENT IN it. A
 *     pipeline has one measured duration and it is credited to the first
 *     stage only (crediting every stage inflated the true total 3.05x), so
 *     `find … | xargs pylint` counts entirely toward `find`. The card's own
 *     description says so in the visible label, and `Explain` carries the
 *     concrete example one hover further.
 *  2. `censored_calls` is a truncated measurement, not a cost — many
 *     durations cluster at the harness's own timeout ceiling. A row that
 *     hides this is a ranking it has not earned, so it renders inline, not
 *     behind a tooltip.
 *  3. `background_calls` is invisible in the other direction: a backgrounded
 *     call's tool result returns before the command finishes, so its real
 *     duration never reaches this measurement at all. Also inline.
 *  4. The Errors column divides by `error_reportable_calls`, NOT by `calls` —
 *     only some harnesses record a structural failure flag at all, so a row's
 *     calls and its measurable calls are different numbers and the gap varies
 *     per row. Dividing by `calls` published a rate deflated by each row's own
 *     silent share, confidently and invisibly.
 *
 * `unattributed_calls`/`unattributed_ms` get the same treatment `/usage/
 * findings` gives its own cap: the share that could not be ranked is stated,
 * never silently dropped.
 */
export function UsageBashCommands({
  insight,
  failed,
  onRetry,
  retrying,
  className,
}: {
  insight: UsageBashInsightView | undefined;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}): React.ReactNode {
  // The daemon ranks by TOTAL cost (which commands make the cut at all), but
  // the row order shown here is by AVERAGE — the daemon's own selection is
  // left untouched, only the presentation order changes, so a command that
  // is slow per call is not buried under one that is merely called often.
  const rows = [...(insight?.commands ?? [])].sort((a, b) => b.avg_ms - a.avg_ms);
  const unattributedCalls = insight?.unattributed_calls ?? 0;

  return (
    <UsageSection
      icon={<TerminalIcon />}
      title="Bash commands by cost"
      description={
        rows.length > 0 ? (
          <>
            Time in calls <Explain term="bash_call_attribution">led</Explain> by each command
          </>
        ) : undefined
      }
      failed={failed}
      onRetry={onRetry}
      retrying={retrying}
      failure="Bash command costs could not be loaded."
      loading={!insight}
      className={className}
      data-testid="usage-bash-commands"
    >
      {rows.length > 0 ? (
        <>
          <CardScroll>
            <Table>
              <TableHeader>
                <TableRow>
                  {/* `Command` is CAPPED, not `LABEL_COL`: a leading executable
                      is a short string ("git", "uv", "python3"), so handing it
                      the 100%-remainder claimed ~63% of a 760px card for
                      four-character words. Capped, it sizes to its own longest
                      value.

                      The surplus has to land SOMEWHERE, though, and that is the
                      half of this the first attempt got wrong: with every
                      column at `w-px` the table had NO declared remainder, so
                      the browser handed the leftover to the last column anyway
                      — the same stretched-blank column, relocated from first to
                      last and measured worse (502px of 760px for a column whose
                      usual value is one em dash). `table-columns.ts` requires
                      exactly one remainder column; declaring it is what turns
                      an accident into a decision.

                      `Measurement` is the honest holder: it is the only column
                      carrying PROSE ("812 capped, 340 background"), so width is
                      the one thing it can actually spend, and blank space in a
                      trailing tertiary note column reads as "nothing to note"
                      rather than as a stretched identifier. */}
                  <TableHead className={`${CAPPED_COL} ${HEAD_CELL}`}>Command</TableHead>
                  <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Calls</TableHead>
                  <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Total</TableHead>
                  <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Avg</TableHead>
                  <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Errors</TableHead>
                  <TableHead className={`${LABEL_COL} ${HEAD_CELL} whitespace-nowrap`}>
                    Measurement
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.executable}>
                    <TableCell className={`${CAPPED_CELL} font-mono`} title={row.executable}>
                      {row.executable}
                    </TableCell>
                    <TableCell className={NUMERIC}>
                      <AbbreviatedNumber value={row.calls} />
                    </TableCell>
                    <TableCell className={NUMERIC}>{duration(row.total_ms)}</TableCell>
                    <TableCell className={NUMERIC}>{duration(row.avg_ms)}</TableCell>
                    <TableCell className={NUMERIC}>{errorRate(row)}</TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-content-tertiary">
                      {measurementNote(row)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardScroll>
          {unattributedCalls > 0 ? (
            <p className="text-xs text-content-tertiary">
              <AbbreviatedNumber value={unattributedCalls} /> call
              {unattributedCalls === 1 ? "" : "s"} ({duration(insight?.unattributed_ms)}) had no
              leading command Grove could parse and are not ranked above.
            </p>
          ) : null}
        </>
      ) : (
        <p className="text-sm text-content-tertiary">
          Not measured: no Bash calls recorded for this selection.
        </p>
      )}
    </UsageSection>
  );
}

/**
 * The two ways a row's ranking can mislead, stated where the ranking is —
 * never only in a tooltip. `censored_calls` reads as "capped" because the
 * daemon measured a ceiling, not a duration; `background_calls` reads as
 * "background" because Grove never saw when the command actually finished.
 */
function measurementNote(row: UsageBashCommandView): React.ReactNode {
  const parts: string[] = [];
  if (row.censored_calls > 0) parts.push(`${row.censored_calls} capped`);
  if (row.background_calls > 0) parts.push(`${row.background_calls} background`);
  return parts.length > 0 ? parts.join(", ") : "—";
}

/**
 * The share of a command's calls whose tool result reported an error — ranked
 * by cost alone, a command that fails constantly and one that never does are
 * indistinguishable, so this is the column that says which is which.
 *
 * THREE outcomes, and each of the two boring ones is a trap in the opposite
 * direction:
 *
 * A MEASURED ZERO RENDERS AS `0%`, never as an absent-value glyph. Zero errors
 * over a population that COULD have reported them is a measurement, and this
 * column's whole job is to separate "never fails" from "fails constantly".
 * Rendering the reliable commands (`pytest`, `mypy`, `poetry` — 35 of 100
 * ranked rows on the reference host) as "no data" defeats the column on exactly
 * the rows a reader is most reassured to see.
 *
 * AN UNMEASURABLE ROW RENDERS AS `NOT_MEASURED`, never as `0%`. The denominator
 * is `error_reportable_calls`, not `calls`, because only some harnesses record
 * a structural failure flag: Claude Code fills `is_error` natively, Codex's
 * tool-output record has no error key in any version, so a wholly-Codex row has
 * a real error count of zero that means "nobody could tell us" rather than
 * "nothing failed". Dividing by `calls` shipped BOTH errors at once — a
 * fabricated `0%` on the unmeasurable rows, and a silently deflated rate on
 * every mixed row (each by its own, different share).
 *
 * Both halves are the same rule the project states as unmeasured-is-never-zero,
 * pointed in both directions: a fabricated absence is as misleading as a
 * fabricated measurement.
 */
function errorRate(row: UsageBashCommandView): React.ReactNode {
  const reportable = row.error_reportable_calls;
  if (reportable <= 0) {
    return (
      <span
        className="text-content-tertiary"
        title={`None of these ${exact(row.calls)} calls ran under a harness that records tool errors, so a failure here is indistinguishable from a success.`}
      >
        {NOT_MEASURED}
      </span>
    );
  }
  return (
    <span
      title={`${exact(row.error_calls)} of ${exact(reportable)} calls that can report an error${
        reportable < row.calls ? ` (of ${exact(row.calls)} calls in total)` : ""
      }`}
    >
      {percent((row.error_calls / reportable) * 100)}
    </span>
  );
}
