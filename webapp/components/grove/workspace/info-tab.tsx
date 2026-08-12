"use client";

import {
  ActivityIcon,
  ClockIcon,
  FingerprintIcon,
  GitBranchIcon,
  GitForkIcon,
  ListChecksIcon,
  PowerIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { WorkspaceActivityView, WorkspacePeekView } from "@/lib/grove/api";
import { baseBranchOf, lastActivityIso } from "@/lib/grove/adapters";
import { IdentityBadges } from "./identity";
import { LifecycleActions } from "./lifecycle-actions";
import { CardGrid, SectionCard, CardField, CardFields, CardStat } from "@/components/grove/card";
import { duration } from "@/components/grove/duration";
import { Explain } from "@/components/grove/glossary";
import { PreciseAge } from "@/components/grove/relative-time";
import { PhaseMeter, TicketRollupMeter } from "./phase-meter";
import { TicketRefsCard } from "./ticket-refs";
import {
  activityStats,
  sessionClocks,
  sessionLatency,
  ticketRollup,
  tokenClassStats,
} from "./selectors";

/**
 * The workspace's whole identity read, in reading order: what the work IS
 * (task phase, linked tickets), what it is DOING (activity), what it IS
 * (identity chips, branch), and what may be done to it (lifecycle).
 *
 * Deliberately omits `worktree_path`: a host filesystem path is host-private
 * and is never rendered anywhere in this UI.
 */
export function InfoTab({
  peek,
  activity,
  onKilled,
}: {
  peek: WorkspacePeekView;
  activity: WorkspaceActivityView | null;
  onKilled: () => void;
}) {
  const state = peek.state;
  const stats = activityStats(activity);
  const tokenClasses = tokenClassStats(activity);
  const todo = activity?.todo;
  const base = baseBranchOf(state);
  const clocks = sessionClocks(activity);
  const latency = sessionLatency(activity);
  const rollup = ticketRollup(state.ticket_refs, activity?.phase ?? null);

  return (
    // Two columns once the PANEL — not the window — is wide enough. In split
    // view this surface is half a screen, so a viewport breakpoint would pair
    // cards in a column too narrow for either.
    <CardGrid className="@xl:grid-cols-2" data-testid="info-tab">
      <SectionCard
        icon={<ListChecksIcon />}
        title="Task"
        description="What the agent says it is doing about the job."
        action={
          todo ? (
            <Badge variant="outline" className="tabular-nums" data-testid="todo-progress">
              {todo.completed}/{todo.total} todos
            </Badge>
          ) : undefined
        }
        className="@xl:col-span-2"
      >
        {/* THE AGGREGATE LEADS, and the workspace's own track follows it, because
            the two answer different questions and a reader wants them in that
            order. "How much of this batch is left" is what someone opens a
            multi-ticket workspace to find out; "which step is the agent on"
            only becomes the headline when there is exactly one thing to be on a
            step of — which is precisely the case where `ticketRollup` returns
            null and this collapses back to the single meter it has always been.

            Both are kept rather than one replacing the other: the track is the
            only place the agent's own note appears, and a rollup can say a
            batch is 60% done while saying nothing about what is happening right
            now. */}
        <TicketRollupMeter rollup={rollup} />
        {rollup && activity?.phase && <hr className="border-border" />}
        <PhaseMeter phase={activity?.phase ?? null} />
        {!activity?.phase && !todo && !rollup && (
          <p className="text-xs text-content-tertiary">Nothing reported yet.</p>
        )}
      </SectionCard>

      <SectionCard icon={<ActivityIcon />} title="Activity" description="This session so far">
        {stats ? (
          <>
            {/* `auto-fit` rather than a column count: figures sit on one line
                in a wide pane and fold to two by two in a docked one, with no
                breakpoint to keep in sync. */}
            <div
              className="grid grid-cols-[repeat(auto-fit,minmax(5rem,1fr))] gap-3"
              data-testid="metrics"
            >
              {stats.map((stat) => (
                <CardStat key={stat.label} label={stat.label} value={stat.value} />
              ))}
            </div>
            {/* The folded `tokens in` figure can read in the hundreds of
                millions — correct (cache reads legitimately dwarf every other
                class) but alarming with nothing beside it to explain the
                magnitude. This unfolds it into the classes that sum to it,
                the moment the wire carries them (`activityStats` drops the
                folded stat above in that same case, so the two never both
                render). */}
            {tokenClasses && (
              <CardFields data-testid="token-classes">
                {tokenClasses.map((row) => (
                  <CardField
                    key={row.label}
                    label={row.term ? <Explain term={row.term}>{row.label}</Explain> : row.label}
                  >
                    {row.value}
                  </CardField>
                ))}
              </CardFields>
            )}
          </>
        ) : (
          <p className="text-xs text-content-tertiary" data-testid="metrics">
            No live session to measure.
          </p>
        )}
      </SectionCard>

      <SectionCard icon={<FingerprintIcon />} title="Identity">
        <IdentityBadges state={state} />
      </SectionCard>

      {/* TWO chips, matching the Identity card beside it. The earlier shape put
          the branch on a badge and the base branch in a bare tertiary span, on
          the reasoning that only the branch you are ON is a state worth marking.
          The same review that flattened Identity found the same defect here: a
          pill followed by loose prose reads as one marked fact and one stray
          one, and the eye cannot tell that `from` introduces a ref rather than
          a sentence. Both are refs, so both take an edge and a glyph, and RANK
          is carried by which glyph — `GitBranch` for the branch that moves,
          `GitFork` for the settled origin.

          The no-base case stays a SENTENCE and not a chip: there is nothing to
          mark, because this workspace adopted the repo's live checkout. A chip
          reading "none" would be an edge drawn around an absence. */}
      <SectionCard icon={<GitBranchIcon />} title="Branch">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <Badge variant="outline" className="min-w-0" title="This workspace's branch">
            <GitBranchIcon aria-hidden className="size-[1em] shrink-0" />
            <span className="sr-only">Branch: </span>
            <span className="max-w-56 truncate font-mono">{state.branch}</span>
          </Badge>
          {base ? (
            <Badge variant="outline" className="min-w-0" title={`Forked from ${base}`}>
              <GitForkIcon aria-hidden className="size-[1em] shrink-0" />
              <span className="sr-only">Forked from: </span>
              <span className="max-w-56 truncate font-mono">{base}</span>
            </Badge>
          ) : (
            <span
              className="text-xs text-content-tertiary"
              title="This workspace runs in the repo's own checkout, so its branch is the base"
            >
              no separate base branch
            </span>
          )}
        </div>
      </SectionCard>

      {/* Ages, not instants: "is this still moving" is the question a timeline
          is asked, and the exact instant stays one hover away rather than being
          the thing you have to parse first. `Updated` is the newer of the two
          and sits first for that reason.

          `PreciseAge`, NOT `RelativeTime` — see its docstring. The rail's
          formatter keeps only the largest whole unit, so a workspace touched
          five hours and fifty minutes ago read "5h ago" here and threw away the
          fifty. This surface is opened deliberately to read one workspace, which
          is exactly where that rounding costs the reader the thing they came
          for; the scale runs to years so a long-lived workspace never reports an
          age anyone has to divide.

          No `mono`: these are the call sites that made `CardField`'s flag look
          like it meant "identifiers and timestamps". An age is a quantity you
          read, not a literal you would retype.

          The session's two clocks now ride the SAME `activity` object as
          everything else here, which is what makes them free. That was the
          open question this comment used to record: neither `WorkspacePeekView`
          nor `WorkspaceActivityView` carried a `DurationView`, only the session
          catalog behind `GET /sessions` did, and the tempting fix was a second
          network call threaded through this component to a route this tab has
          no other reason to know about. The honest one was the daemon-side
          field — `SessionActivityView.duration`, mirroring what the catalog
          already computes — so the rows below cost this card no fetch, no
          loading state and no failure mode of their own.

          BOTH CLOCKS OR NEITHER, and never one relabelled as "duration". They
          diverge by design: ten sub-agents running ten minutes side by side
          honestly read `10m` and `100m`, and a surface that picks one teaches
          the reader that the other is wrong. Explained in place rather than in
          docs, because "compute exceeds wall clock" reads as a bug until you
          know sub-agents are summed. */}
      <SectionCard icon={<ClockIcon />} title="Timeline">
        <CardFields>
          {/* `lastActivityIso`, NOT `state.updated_at` — see its docstring.
              The record's own stamp only moves when GROVE writes the record, so
              a workspace whose agent had been working continuously for four
              hours reported itself four hours idle. The rail has always shown
              the derived instant; this card was reading the raw field and
              labelling it "Updated", which a reader takes to mean "last did
              something". Falls back to the record's stamp inside the
              derivation, so a session-less workspace still reports an age. */}
          <CardField label="Last activity">
            <PreciseAge iso={activity ? lastActivityIso(activity) : state.updated_at} />
          </CardField>
          <CardField label="Created">
            <PreciseAge iso={state.created_at} />
          </CardField>
          {state.paused_at && (
            <CardField label="Paused">
              <PreciseAge iso={state.paused_at} />
            </CardField>
          )}
          {/* Rendered only when the session reports them at all. A workspace
              with no live session, or one whose transcript Grove could not
              time, would otherwise print two rows of "not measured" — which
              claims the clocks exist and read zero rather than that nothing
              was measured. `duration()` already refuses a fabricated zero for
              a single absent field; this guard is the same rule one level up. */}
          {clocks && (
            <>
              <CardField label={<Explain term="clock_time" />}>
                {duration(clocks.active_ms)}
              </CardField>
              <CardField label={<Explain term="compute_time" />}>
                {duration(clocks.execution_ms)}
              </CardField>
              {/* Compute time's two halves, and the reason they are worth a
                  row each: "this session has burned four hours" is the same
                  number whether the model was slow or the test suite was, and
                  the two take opposite actions. They ADD UP to the row above —
                  a partition of it, never a second measurement — which is why
                  they sit directly under it and are indented by nothing: a
                  reader who checks the arithmetic should find it works.

                  A vertical field list can afford all four where the session
                  TABLES cannot, which is why those show the two halves in
                  place of the total instead. Same numbers, different budget. */}
              <CardField label={<Explain term="model_wait" />}>
                {duration(clocks.generation_ms)}
              </CardField>
              <CardField label={<Explain term="tool_time" />}>{duration(clocks.tool_ms)}</CardField>
            </>
          )}
          {/* Only once a generation had a measurable interval — the same
              absence-is-not-a-value rule `clocks` follows one line up. A
              session with no timed generation yet (nothing sent to the model)
              is not the same fact as a model that answered instantly. */}
          {latency && latency.calls > 0 && (
            <CardField label={<Explain term="model_latency" />}>
              {duration(latency.avg_ms)}
            </CardField>
          )}
        </CardFields>
      </SectionCard>

      {/* UNCONDITIONAL, unlike the chip row it replaced. "This workspace tracks
          nothing" is a fact a reader needs and an invitation to fix, where an
          absent card is indistinguishable from a feature that does not exist —
          which is exactly how it was read. The card owns its own header and
          states because both depend on data only it fetches.

          It sits AFTER Timeline: a ticket is the least volatile thing on this
          tab and the one most often absent, so leading with it put the card most
          likely to be empty above the two that always say something.

          The phase goes in so each row can show GROVE'S progress on that ticket
          beside the tracker's own status. It is the same object the Task card
          above already renders, handed down rather than fetched — both halves of
          the join are on this tab, so the card gains no request. */}
      <TicketRefsCard
        repoRoot={state.repo_root}
        refs={state.ticket_refs}
        phase={activity?.phase ?? null}
      />

      <SectionCard
        icon={<PowerIcon />}
        title="Lifecycle"
        description="The engine is the real gate; these are the verbs it will accept."
        className="@xl:col-span-2"
      >
        <LifecycleActions state={state} onKilled={onKilled} />
      </SectionCard>
    </CardGrid>
  );
}
