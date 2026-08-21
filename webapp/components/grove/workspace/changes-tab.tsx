"use client";

import { GitCommitHorizontalIcon, GitCompareArrowsIcon } from "lucide-react";

import { Timeline } from "@/components/elements/timeline";
import type { CommitSummaryView } from "@/lib/grove/api";
import { CardGrid, SectionCard, CardScroll, CardStat } from "@/components/grove/card";
import { Skeleton } from "@/components/ui/skeleton";
import { baseBranchOf } from "@/lib/grove/adapters";
import { commitEvents, type WorkspaceRead } from "./selectors";

/**
 * What this branch has actually done: divergence from base, working-tree churn,
 * and the fork-point-filtered commit list.
 *
 * There is no hunk view because the daemon exposes no per-file patch for the
 * working tree — `/commits` and the peek's counters are the whole wire surface.
 * The Files tab covers the per-file diffs the agent itself reported.
 *
 * `peek` is the narrowed `WorkspaceRead`, not `WorkspacePeekView`, so the public
 * share view renders this tab verbatim — see that type's docstring for why the
 * narrowing is the mechanism rather than a tidy-up.
 */
export function ChangesTab({
  peek,
  commits,
}: {
  peek: WorkspaceRead;
  commits: CommitSummaryView[] | undefined;
}) {
  // `undefined` is NOT `[]`, and collapsing the two is a wrong answer rather
  // than a missing one: `commits` is undefined while `useWorkspaceCommits` is
  // still in flight, and `commitEvents(commits ?? [])` reported "No commits on
  // this branch yet" — a confident, false claim about a branch that may have
  // fifty. `FilesTab` already draws this distinction; this card did not.
  const loading = commits === undefined;
  const events = commitEvents(commits ?? []);
  const base = baseBranchOf(peek.state);

  // The THIRD case, after `undefined` (loading) and `[]` (measured empty): the
  // daemon had no recorded fork point, so it answered a time-bounded question
  // instead — everything on the branch since the workspace was created. That
  // list can include commits this workspace did not make, so the card must not
  // present it with the precision of a fork-point answer.
  //
  // Read off the ROWS, because only the daemon knows which range it ran; the
  // scope is a property of the answer and a bare array has nowhere else to
  // carry one. The empty answer has no row to read, so it falls back to the
  // same fact the daemon branched on. `=== null` and not falsy: the daemon
  // always sends the key (string or null), and an ABSENT one means this is not
  // a daemon payload at all, which must keep the historical reading.
  const timeBounded =
    commits && commits.length > 0
      ? commits[0].scope === "since_created_at"
      : peek.state.base_commit === null;
  const sinceLabel = timeBounded ? "since this workspace was created" : "since the fork point";

  return (
    <CardGrid data-testid="changes-tab">
      <SectionCard
        icon={<GitCompareArrowsIcon />}
        title="Divergence"
        description={
          base ? (
            <>
              against <span className="font-mono">{base}</span>
            </>
          ) : (
            "against the commit this workspace started from"
          )
        }
      >
        {/* `auto-fit`, like the Activity card: the figures fill whatever width
            the pane has instead of stepping at a breakpoint someone has to
            keep in sync with the number of stats.

            AHEAD AND BEHIND ARE WITHHELD WITHOUT A BASE BRANCH, not printed as
            zero. They are the two figures the daemon still derives from the
            base NAME — deliberately, because "behind" asks how far the base has
            moved and a frozen commit can only ever answer zero — so with no
            separate base the range is `HEAD..branch`, which is structurally
            empty however much work has been done. A zero that cannot be
            anything else is not a measurement. The other three are anchored on
            `base_commit` and stay true. */}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(5rem,1fr))] gap-3">
          {base ? (
            <>
              <CardStat label="ahead" value={peek.base_ahead} />
              <CardStat label="behind" value={peek.base_behind} />
            </>
          ) : null}
          <CardStat label="dirty" value={peek.dirty_files} />
          <CardStat label="added" value={`+${peek.diff_added}`} tone="positive" />
          <CardStat label="removed" value={`−${peek.diff_removed}`} tone="negative" />
        </div>
      </SectionCard>

      <SectionCard
        icon={<GitCommitHorizontalIcon />}
        title="Commits"
        description={
          <>
            {loading || events.length === 0 ? sinceLabel : `${events.length} ${sinceLabel}`}
            {timeBounded ? (
              <span className="block">
                No fork point was recorded for this workspace, so this is everything that landed
                on the branch in that window — it may include commits the workspace did not make.
              </span>
            ) : null}
          </>
        }
        flush={!loading && events.length > 0}
      >
        {loading ? (
          // Three rows, because the honest statement while the log is in
          // flight is "something is coming", not a count and not a denial.
          <div className="flex flex-col gap-2" aria-hidden data-testid="commits-loading">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-3.5 w-full" />
            ))}
          </div>
        ) : events.length === 0 ? (
          // Empty AND time-bounded is still not "no commits on this branch" —
          // that claim is about all of history, and this read only ever looked
          // at a window. Same defect as the loading case, one state further on.
          <p className="text-xs text-content-tertiary">
            {timeBounded ?
              "No commits on this branch since this workspace was created."
            : "No commits on this branch yet."}
          </p>
        ) : (
          // Bounded rather than free-growing: a long-lived branch has hundreds
          // of commits and the card must not become the panel.
          <CardScroll className="max-h-96">
            <Timeline
              events={events}
              visibleCount={events.length}
              className="max-w-none"
              data-testid="commit-timeline"
            />
          </CardScroll>
        )}
      </SectionCard>
    </CardGrid>
  );
}
