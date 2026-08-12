"use client";

import Link from "next/link";

import { AgentMark } from "@/components/grove/agent-mark";
import { duration } from "@/components/grove/duration";
import { Explain } from "@/components/grove/glossary";
import { BranchLabel, LocationLabel, ProjectLabel } from "@/components/grove/entity";
import { RelativeTime } from "@/components/grove/relative-time";
import {
  CAPPED_CELL,
  CAPPED_COL,
  HEAD_CELL,
  LABEL_CELL,
  LABEL_COL,
  NUMERIC,
} from "@/components/grove/table-columns";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { SessionSummaryView } from "@/lib/grove/api";
import { relativeCwd, turnCountOf } from "./filter";

/**
 * The catalog as a table — the primary way a person navigates their whole
 * history of agent sessions.
 *
 * WHY A TABLE AND NOT CARDS. The questions asked here are comparative — which
 * of these ran longest, which is most recent, which are on this branch — and a
 * comparison wants aligned columns. A card wall answers "what is this one" well
 * and "which of these" badly.
 *
 * THE HIERARCHY, and it is the whole treatment: identity first and fixed-width
 * (the id, which is what a bug report quotes), then the two entity columns that
 * absorb the remaining width, then the numbers a reader scans down — turns,
 * then the two durations. Wall clock and Compute are never merged into one
 * column: they are separately-computed reducers over the same intervals (see
 * `components/grove/duration.ts`) and one is routinely 2x+ the other. The
 * numeric columns are `NUMERIC` so their digits line up and they claim no space
 * they do not need; the entity columns are `LABEL_COL`/`LABEL_CELL`, which is
 * this app's already-solved answer to "a long value clipped while there is
 * empty space beside it".
 *
 * Every value that names a THING wears the shared entity vocabulary — a project
 * reads the same here as on a fleet card, a branch the same as in the rail —
 * and every glyph carries its kind in `sr-only` text so the typing survives for
 * a screen reader rather than being purely visual.
 */
export function SessionTable({
  sessions,
}: {
  sessions: readonly SessionSummaryView[];
}): React.ReactNode {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className={`${CAPPED_COL} ${HEAD_CELL}`}>Session</TableHead>
          <TableHead className={`${CAPPED_COL} ${HEAD_CELL}`}>Agent</TableHead>
          {/* The ONE remainder column. See `CAPPED_COL` for why Branch is not
              a second one. */}
          <TableHead className={`${LABEL_COL} ${HEAD_CELL}`}>Location</TableHead>
          <TableHead className={`${CAPPED_COL} ${HEAD_CELL}`}>Branch</TableHead>
          <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>Turns</TableHead>
          {/* Two durations, never one. `Wall clock` is the union of the
              session's active intervals — real time with the waits for a
              human removed — and `Compute` is the same intervals SUMMED
              across the root agent and every sub-agent, so ten sub-agents
              running ten minutes side by side read 10m and 100m. The
              divergence is the measurement, not a double count, which is
              exactly why one column could not carry both. Same vocabulary as
              the usage page's "Recent sessions" table — see
              `components/grove/usage/sessions.tsx`. The one-line definition
              behind each word is the shared glossary entry
              (`components/grove/glossary.tsx`), not a `title` attribute. */}
          <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>
            <Explain term="clock_time">Wall clock</Explain>
          </TableHead>
          <TableHead className={`${NUMERIC} ${HEAD_CELL}`}>
            <Explain term="compute_time">Compute</Explain>
          </TableHead>
          <TableHead className={`${CAPPED_COL} ${HEAD_CELL}`}>Last active</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sessions.map((session) => (
          <SessionRow key={`${session.adapter_kind}-${session.session_id}`} session={session} />
        ))}
      </TableBody>
    </Table>
  );
}

function SessionRow({ session }: { session: SessionSummaryView }): React.ReactNode {
  const turns = turnCountOf(session);
  // A row whose head read never recovered a `cwd` is not drillable: the
  // transcript route resolves by `(kind, cwd, session_id)`, so a link without
  // one 404s by construction. Such rows are still LISTED — hiding them would
  // misreport what is on the host — they simply do not link.
  const cwd = session.cwd ?? null;
  const subPath = relativeCwd(session);

  return (
    // `relative` is what lets the session link stretch over the whole row —
    // see `SessionIdentity`. `focus-within` mirrors the vendored `hover` so a
    // keyboard lands on a row that highlights exactly like a moused one; without
    // it the only feedback is a ring around eight characters of id.
    <TableRow
      className="relative focus-within:bg-muted/50"
      data-testid="session-row"
      data-live={session.live}
    >
      <TableCell className={CAPPED_COL}>
        <span className="flex items-center gap-2">
          <SessionIdentity session={session} cwd={cwd} />
          {session.workspace_title ? (
            // One LINE, not one line each. A second line here made the two rows
            // that have a workspace title 55px and 49px tall against 39px for
            // everything else — a list whose row rhythm visibly stutters twice.
            <span
              className="text-content-tertiary min-w-0 truncate text-xs"
              title={session.workspace_title}
            >
              {session.workspace_title}
            </span>
          ) : null}
          {session.live ? (
            <Badge
              variant="secondary"
              className="shrink-0 px-1.5 text-xs"
              title="An agent is running in this directory now"
            >
              live
            </Badge>
          ) : null}
        </span>
      </TableCell>

      {/* Tertiary like every column but the id: the mark beside it is what
          identifies the harness at a glance, so the word is the fallback for a
          reader who does not know the logo — present, not read. */}
      <TableCell className={`${CAPPED_COL} text-content-tertiary`}>
        <span className="flex items-center gap-1.5">
          <AgentMark agentName={session.adapter_kind} className="size-3.5 shrink-0" />
          <span>{session.adapter_kind}</span>
        </span>
      </TableCell>

      <TableCell className={LABEL_CELL} title={cwd ?? undefined}>
        {session.project ? (
          <span className="flex min-w-0 items-center gap-1.5">
            <ProjectLabel name={session.project.repo_name} className="shrink-0" />
            {/* The worktree under the repo. Without it every Grove session in
                one repo reads identically, and a search that matched the path
                looks like it matched nothing. */}
            {subPath ? (
              <span className="text-content-tertiary min-w-0 truncate text-xs" title={subPath}>
                {subPath}
              </span>
            ) : null}
          </span>
        ) : cwd ? (
          <LocationLabel path={cwd} />
        ) : (
          <Unknown of="location" />
        )}
      </TableCell>

      <TableCell className={CAPPED_CELL}>
        {session.git_branch ? <BranchLabel name={session.git_branch} /> : <Unknown of="branch" />}
      </TableCell>

      <TableCell className={NUMERIC}>
        <TurnCount turns={turns} />
      </TableCell>

      <TableCell className={NUMERIC}>{duration(session.duration?.active_ms)}</TableCell>
      <TableCell className={NUMERIC}>{duration(session.duration?.execution_ms)}</TableCell>

      {/* Capped, because `RelativeTime` is mount-gated: the SERVER paints the
          absolute `8/10/2026, 3:11:29 PM`, so an uncapped column is sized by
          that on first paint and then snaps to the width of "2h ago". The cap
          holds the column still; the exact value is in the element's own
          title either way. */}
      <TableCell className={`text-content-tertiary ${CAPPED_COL} max-w-28 truncate`}>
        <RelativeTime iso={session.modified_at} />
      </TableCell>
    </TableRow>
  );
}

/**
 * The row's IDENTITY — the session id, and only the session id.
 *
 * This column used to fall back to the branch, which is the defect: with a
 * catalog row carrying no parsed title, the label rule dropped through to
 * `git_branch` on effectively every row, so the Session column and the Branch
 * column printed the same string and neither said which transcript you were
 * about to open. An id is the one field a catalog row always has and the one
 * thing the column is for; the human context lives in the columns beside it and
 * in the workspace title underneath.
 *
 * Shortened to the first segment, with the whole id one hover away — the same
 * bargain the usage tables strike, and the same one `RelativeTime` strikes: an
 * approximation to scan, the exact value never destroyed.
 *
 * THE WHOLE ROW IS THE TARGET, and this is the element that makes it one:
 * `after:absolute after:inset-0` stretches an overlay from this link across the
 * row's positioning context (`TableRow` is `relative`). One link per row rather
 * than a click handler on the `tr`, because that keeps the row a single tab
 * stop with a real `href` — reachable by keyboard, openable in a new tab, and
 * announced with a destination — none of which a `div` with an `onClick` gets.
 * `cursor-pointer` rides the overlay rather than the row, so the two rows in a
 * hundred that cannot be opened do not claim to be clickable.
 */
function SessionIdentity({
  session,
  cwd,
}: {
  session: SessionSummaryView;
  cwd: string | null;
}): React.ReactNode {
  const short = session.session_id.slice(0, 8);
  if (cwd === null) {
    return (
      <span
        className="font-mono text-xs"
        title={`${session.session_id} — no recorded location, so this transcript cannot be opened`}
      >
        {short}
      </span>
    );
  }
  return (
    <Link
      href={{
        pathname: `/sessions/${encodeURIComponent(session.session_id)}`,
        query: { kind: session.adapter_kind, cwd },
      }}
      className="font-mono text-xs underline-offset-2 after:absolute after:inset-0 after:cursor-pointer hover:underline"
      title={session.session_id}
    >
      {short}
    </Link>
  );
}

/**
 * A turn count, or an em dash for a session nobody counted.
 *
 * NEVER `0`. The host-wide scan is metadata-only, so an absent count means "not
 * counted at this scope", not "this session did nothing" — the same rule the
 * audit follows for unmeasured spend. The dash carries a title rather than
 * standing mute, because a bare `—` in a numeric column reads as a rendering
 * failure to anyone who has not been told.
 */
function TurnCount({ turns }: { turns: number | null }): React.ReactNode {
  if (turns === null) {
    return (
      <span className="text-content-tertiary" title="Not counted at this scope">
        —
      </span>
    );
  }
  return <span className="tabular-nums">{turns.toLocaleString("en-US")}</span>;
}

/** A field the scan genuinely did not recover. Muted and named, never blank —
 * an empty cell is indistinguishable from a component that failed. */
function Unknown({ of }: { of: string }): React.ReactNode {
  return (
    <span className="text-content-tertiary" title={`No ${of} recorded for this session`}>
      —
    </span>
  );
}
