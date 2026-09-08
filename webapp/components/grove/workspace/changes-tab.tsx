"use client";

import { GitCommitHorizontalIcon, GitCompareArrowsIcon } from "lucide-react";

import type { CommitSummaryView } from "@/lib/grove/api";
import {
  CardCell,
  CardGrid,
  SectionCard,
  CardScroll,
} from "@/components/grove/card";
import { RelativeTime } from "@/components/grove/relative-time";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { baseBranchOf } from "@/lib/grove/adapters";
import { cn } from "@/lib/utils";
import {
  divergenceFacts,
  type PanelTab,
  type WorkspaceRead,
} from "./selectors";

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
 *
 * A VENDORED ELEMENT THAT DRAWS ITS OWN SURFACE IS STANDALONE OR ABSENT. Timeline
 * brings its own paper card, so the commits compose divided rows in this card.
 */
export function ChangesTab({
  peek,
  commits,
  onNavigate,
}: {
  peek: WorkspaceRead;
  commits: CommitSummaryView[] | undefined;
  onNavigate?: (tab: PanelTab) => void;
}) {
  // `undefined` is NOT `[]`, and collapsing the two is a wrong answer rather
  // than a missing one: `commits` is undefined while `useWorkspaceCommits` is
  // still in flight, and `commitEvents(commits ?? [])` reported "No commits on
  // this branch yet" — a confident, false claim about a branch that may have
  // fifty. `FilesTab` already draws this distinction; this card did not.
  const loading = commits === undefined;
  const commitList = commits ?? [];
  const base = baseBranchOf(peek.state);
  const facts = divergenceFacts(peek, base !== null);

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
  const sinceLabel = timeBounded
    ? "since this workspace was created"
    : "since the fork point";
  const unchanged =
    !loading &&
    commitList.length === 0 &&
    peek.base_ahead === 0 &&
    peek.base_behind === 0 &&
    peek.dirty_files === 0 &&
    peek.diff_added === 0 &&
    peek.diff_removed === 0;

  if (unchanged) {
    return (
      <CardGrid data-testid="changes-tab" data-source="clean">
        <MeasuredEmptyChanges
          timeBounded={timeBounded}
          onNavigate={onNavigate}
        />
      </CardGrid>
    );
  }

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
        {/* THE SAME BOUNDED CELLS THE ACTIVITY CARD USES, because these are the
            same object at a different range — what this session did, and what
            this branch did. An `auto-fit` grid of bare figures let five
            measurements of three different scopes read as one row of numbers;
            a perimeter around each label-and-value pair is what says they are
            five separate facts.

            Columns respond to THIS card's width, and both fact counts divide
            without an orphan at every step: five folds 5 → 3 → 2 and three
            folds 3 → 2. No placeholder is ever reserved for a withheld figure.

            AHEAD AND BEHIND ARE WITHHELD WITHOUT A BASE BRANCH — see
            `divergenceFacts` for why a zero there is not a measurement. */}
        <div className="@container/div min-w-0">
          <div
            className={cn(
              // `rem` steps, so the columns fold under text enlargement as well
              // as under a narrow panel — see the Activity grid for the whole
              // argument; this is the same grid at a different range.
              "grid grid-cols-1 gap-2 @min-[15rem]/div:grid-cols-2",
              facts.length === 5
                ? "@sm/div:grid-cols-3 @2xl/div:grid-cols-5"
                : "@sm/div:grid-cols-3",
            )}
            data-testid="divergence-metrics"
            data-facts={facts.length}
          >
            {facts.map((fact) => (
              <CardCell
                key={fact.key}
                data-testid={`divergence-${fact.key}`}
                icon={<fact.icon aria-hidden />}
                label={fact.label}
                value={fact.value}
                title={fact.title}
                tone={fact.tone}
              />
            ))}
          </div>
        </div>
      </SectionCard>

      <SectionCard
        icon={<GitCommitHorizontalIcon />}
        title="Commits"
        description={
          <>
            {loading || commitList.length === 0
              ? sinceLabel
              : `${commitList.length} ${sinceLabel}`}
            {timeBounded ? (
              <span className="block">
                No fork point was recorded for this workspace, so this is
                everything that landed on the branch in that window — it may
                include commits the workspace did not make.
              </span>
            ) : null}
          </>
        }
        flush={!loading && commitList.length > 0}
      >
        {loading ? (
          // Three rows, because the honest statement while the log is in
          // flight is "something is coming", not a count and not a denial.
          <div
            className="flex flex-col gap-2"
            aria-hidden
            data-testid="commits-loading"
          >
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-3.5 w-full" />
            ))}
          </div>
        ) : commitList.length === 0 ? (
          // Empty AND time-bounded is still not "no commits on this branch" —
          // that claim is about all of history, and this read only ever looked
          // at a window. Same defect as the loading case, one state further on.
          <EmptyDivergence timeBounded={timeBounded} />
        ) : (
          <CardScroll className="max-h-96">
            <ul
              className="divide-y divide-border"
              data-testid="commit-timeline"
            >
              {commitList.map((commit) => (
                <li
                  key={commit.sha}
                  className="flex min-w-0 items-baseline gap-2 px-3 py-2"
                >
                  <span className="shrink-0 font-mono text-xs text-content-tertiary">
                    {commit.sha.slice(0, 7)}
                  </span>
                  <span aria-hidden className="text-content-tertiary">
                    ·
                  </span>
                  <span className="min-w-0 flex-1 break-words text-sm text-content-primary">
                    <GitmojiSubject subject={commit.subject} />
                  </span>
                  <RelativeTime
                    iso={commit.committed_at}
                    className="w-16 shrink-0 truncate text-end text-xs text-content-tertiary"
                  />
                </li>
              ))}
            </ul>
          </CardScroll>
        )}
      </SectionCard>
    </CardGrid>
  );
}

/** A measured empty belongs in the same low-key alert shape as other tab empties. */
function EmptyDivergence({ timeBounded }: { timeBounded: boolean }) {
  return (
    <Alert data-testid="commits-empty">
      <GitCommitHorizontalIcon aria-hidden />
      <AlertTitle>No commits in this range</AlertTitle>
      <AlertDescription>
        <p className="text-xs text-content-tertiary">
          {timeBounded
            ? "No commits on this branch since this workspace was created."
            : "No commits on this branch yet."}
        </p>
        <a href="#changes" className="text-xs underline underline-offset-2">
          Review branch changes
        </a>
      </AlertDescription>
    </Alert>
  );
}

/** The tab-level empty only renders after every divergence measurement answered zero. */
function MeasuredEmptyChanges({
  timeBounded,
  onNavigate,
}: {
  timeBounded: boolean;
  onNavigate?: (tab: PanelTab) => void;
}) {
  return (
    <Alert data-testid="changes-empty">
      <GitCompareArrowsIcon aria-hidden />
      <AlertTitle>Nothing has diverged from the fork point</AlertTitle>
      <AlertDescription>
        <p className="text-xs text-content-tertiary">
          {timeBounded
            ? "The workspace has no branch or working-tree changes since it was created."
            : "The branch and working tree match the fork point."}
        </p>
        {onNavigate && (
          <Button
            variant="link"
            size="sm"
            className="h-auto p-0 text-xs"
            onClick={() => onNavigate("info")}
          >
            View workspace details
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}

/** Keep emoji at the subject's size instead of letting a color-font glyph dominate its row. */
function GitmojiSubject({ subject }: { subject: string }) {
  const match = subject.match(
    /^(\p{Extended_Pictographic}(?:️|‍\p{Extended_Pictographic})*\s+)(.*)$/u,
  );
  if (!match) return subject;
  return (
    <>
      <span className="text-[1em]" aria-hidden>
        {match[1]}
      </span>{" "}
      {match[2]}
    </>
  );
}
