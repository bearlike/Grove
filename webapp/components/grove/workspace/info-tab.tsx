"use client";

import {
  ActivityIcon,
  ClockIcon,
  ListChecksIcon,
  OctagonAlertIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { WorkspaceStateView } from "@/lib/grove/api";
import { newestActivityIso, phaseEnteredAt } from "@/lib/grove/adapters";
import { useWorkspaceHistory } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import { WorkspaceIdentityCard } from "./identity";
import {
  CardCell,
  CardGrid,
  SectionCard,
  CardField,
  CardFields,
} from "@/components/grove/card";
import { duration } from "@/components/grove/duration";
import { Explain } from "@/components/grove/glossary";
import { PreciseAge } from "@/components/grove/relative-time";
import { ContextMeter } from "./context-meter";

/** Four decimals: a single cheap turn is fractions of a cent, and two would print $0.00. */
const USD = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
import { ChecklistMeter, PhaseMeter, phaseSummary } from "./phase-meter";
import { TicketRefsCard } from "./ticket-refs";
import { WorkspaceHistoryDialog } from "./history-dialog";
import {
  activityFacts,
  agentIsWorking,
  sessionClocks,
  nativeFacts,
  sessionLatency,
  type ActivityRead,
  type PanelTab,
  type WorkspaceRead,
} from "./selectors";

/**
 * The workspace's whole identity read, in reading order: what the work IS (task
 * phase), what it is DOING (activity), what it is doing it ABOUT (tickets),
 * when (timeline), and finally what it IS (the owner-only identity card).
 *
 * THIS TAB IS A READ. Every verb — pause, resume, respawn, kill, send keys —
 * lives on Controls, so nothing here acts on the workspace. That split is why
 * the tab can be read top to bottom without a reader ever having to check
 * whether the next thing they scroll past is a button.
 *
 * Deliberately omits `worktree_path`: a host filesystem path is host-private
 * and is never rendered anywhere in this UI.
 *
 * ONE COMPONENT SERVES TWO AUDIENCES, and the props are what make that safe
 * rather than a branch inside the body:
 *
 * - `peek` and `activity` are the narrowed `WorkspaceRead` / `ActivityRead`
 *   shapes (see `selectors.ts`), so the public share payload — which carries no
 *   host path at all — satisfies them structurally and this file needs no
 *   knowledge that a public view exists.
 * - `identity` is OPTIONAL and is the FULL record, which is what makes the
 *   owner-only card unforgeable: the narrowed `WorkspaceRead.state` above
 *   cannot satisfy it, so a caller cannot ask for the card without producing a
 *   record that supports it. It is the data AND the permission, never a
 *   `readOnly` boolean beside it that could disagree with itself.
 *
 *   Withholding it is a PRODUCT decision, not the security boundary — that is
 *   the daemon's three read-only routes. Somebody reading a shared link came
 *   for the WORK: the task, the activity, the tickets, the timeline. An agent
 *   name, a placement, a runtime and a pair of branch refs are machinery they
 *   cannot act on, and on a page whose whole job is to be readable by a
 *   stranger they are noise.
 * - `repoRoot` is `null` to mean "do not resolve tickets from the browser" —
 *   a client-side resolve spends the host's tracker credential, which an
 *   anonymous reader must never be able to spend, and needs a repo path the
 *   public payload does not carry. The public view gets its ticket titles
 *   resolved daemon-side instead, already on the refs by the time they arrive.
 */
export function InfoTab({
  peek,
  activity,
  repoRoot,
  identity,
  onNavigate,
}: {
  peek: WorkspaceRead;
  activity: ActivityRead | null;
  repoRoot: string | null;
  identity?: WorkspaceStateView;
  onNavigate?: (tab: PanelTab) => void;
}) {
  const state = peek.state;
  const facts = activityFacts(activity);
  const todo = activity?.todo;
  const phase = activity?.phase ?? null;
  const history = useWorkspaceHistory(repoRoot === null ? null : state.id);
  const enteredAt = phase ? phaseEnteredAt(phase, history.data?.progress) : null;
  const clocks = sessionClocks(activity);
  const latency = sessionLatency(activity);
  const native = nativeFacts(activity);
  const hasChecklist = (todo?.total ?? 0) > 0;

  return (
    // Two columns once the PANEL — not the window — is wide enough. In split
    // view this surface is half a screen, so a viewport breakpoint would pair
    // cards in a column too narrow for either.
    <CardGrid className="@xl:grid-cols-2" data-testid="info-tab">
      {/* NO SUBTITLE BAND on any of the three cards below. A header that names a
          topic and then explains it costs a second row on the densest surface in
          the app — 70–94px of chrome per card, measured — and the explanation it
          carried is either obvious from the title or belongs on the fact it
          explains. The trailing slot carries the SCOPE or the RECENCY instead,
          which is the thing a reader actually needs beside the title. */}
      <SectionCard
        icon={<ListChecksIcon />}
        title="Task"
        // The history trigger joins this row rather than taking the slot, and
        // the row has to EXIST independently of the phase: `SectionCard` renders
        // `CardAction` only when `action` is truthy, so the old three-way
        // ternary returning `undefined` meant a workspace with recorded history
        // and no current claim — a paused one, whose phase file died with its
        // worktree — had nowhere to put the icon.
        action={
          <span className="flex min-w-0 items-center gap-2">
            {phase?.blocked ? (
              <Badge variant="destructive" data-testid="task-blocked">
                <OctagonAlertIcon aria-hidden />
                Blocked
              </Badge>
            ) : phase ? (
              <span
                className="truncate text-xs text-content-tertiary"
                data-testid="task-phase-summary"
              >
                {phaseSummary(phase)}
              </span>
            ) : null}
            {repoRoot !== null && <WorkspaceHistoryDialog workspaceId={state.id} repoRoot={repoRoot} />}
          </span>
        }
        className="@xl:col-span-2"
      >
        {/* ONE CLAIM PER CARD: this is the WORKSPACE's own latest report, and
            nothing here averages its tickets. A ticket's claim belongs on the
            row that names that ticket, which is where the rollup and the
            per-ticket notes now live — a `Ticket reports` list here restated
            rows that were already on the page a card below.

            `@container/task` is the whole responsive contract for this card: the
            track's labels, and the sentence that replaces them, both read THIS
            card's width. A viewport breakpoint would hand a docked half-window
            panel the layout meant for a full one. */}
        <div className="@container/task flex min-w-0 flex-col gap-2">
          <PhaseMeter
            phase={phase}
            todo={todo}
            working={agentIsWorking(activity)}
            enteredAt={enteredAt}
          />
          <ChecklistMeter todo={todo} />
          {!phase && !hasChecklist && (
            <p
              className="text-sm text-content-tertiary"
              data-testid="task-empty"
            >
              No task report yet. No phase or checklist has been reported.
            </p>
          )}
        </div>
      </SectionCard>

      <SectionCard
        icon={<ActivityIcon />}
        title="Activity"
        action={
          <span
            className="text-xs text-content-tertiary"
            title="Every figure here is read off this workspace's primary agent session, not summed across its sub-agents."
            data-testid="activity-scope"
          >
            Primary session
          </span>
        }
        className="@xl:col-span-2"
      >
        {/* The window sits ABOVE the counts because it is the one figure here
            that says something about the NEXT turn — every fact below is a
            total of what already happened. Absent when the harness has not
            said, never a 0 % (see the meter). */}
        <ContextMeter
          context={activity?.sessions[0]?.activity.context}
          unavailable={activity?.sessions[0]?.activity.context_unavailable_reason}
        />
        {facts ? (
          // The container is the CARD BODY, so the columns respond to the space
          // the cells actually have. Six facts fold 6 → 3 → 2 → 1 and four fold
          // 4 → 2 → 1, and a fact is never orphaned onto a row of its own: every
          // count divides evenly at every step, which a free-flowing `auto-fit`
          // grid cannot promise.
          //
          // THE STEPS ARE IN `rem`, WHICH IS WHAT MAKES THIS RESPOND TO TEXT
          // SIZE AND NOT ONLY TO WIDTH. A container query measures the container
          // in pixels, but a `rem` threshold resolves against the ROOT font
          // size — so a reader at 200% text enlargement crosses every step
          // downward without the panel moving, and the last one to a single
          // column is what keeps a 40px figure from being clipped in a cell
          // sized for a 20px one. Measured: the single-column step is what a
          // 320px viewport at a 32px root actually needs.
          <div className="@container/cells min-w-0">
            <div
              className={cn(
                "grid grid-cols-1 gap-2 @min-[15rem]/cells:grid-cols-2",
                facts.length === 6
                  ? "@md/cells:grid-cols-3 @3xl/cells:grid-cols-6"
                  : "@md/cells:grid-cols-4",
              )}
              data-testid="metrics"
              data-facts={facts.length}
            >
              {facts.map((fact) => (
                <CardCell
                  key={fact.key}
                  data-testid={`metric-${fact.key}`}
                  icon={<fact.icon aria-hidden />}
                  label={
                    fact.term ? (
                      <Explain term={fact.term}>{fact.label}</Explain>
                    ) : (
                      fact.label
                    )
                  }
                  // An unreported class says so in its own cell, quieter than a
                  // figure — never a fabricated `0`, which would claim it was
                  // measured. A genuine `0` is a figure and reads like one.
                  value={fact.value ?? "Not measured"}
                  muted={fact.value === null}
                  derived={fact.derived}
                  title={fact.title}
                />
              ))}
            </div>
          </div>
        ) : (
          <p className="text-sm text-content-tertiary" data-testid="metrics">
            No live session to measure.
          </p>
        )}
      </SectionCard>

      {/* UNCONDITIONAL, unlike the chip row it replaced. "This workspace tracks
          nothing" is a fact a reader needs and an invitation to fix, where an
          absent card is indistinguishable from a feature that does not exist —
          which is exactly how it was read. The card owns its own header and
          states because both depend on data only it fetches.

          It sits DIRECTLY UNDER Activity because those two answer the same
          question at two ranges — what this agent is doing right now, and what
          it is doing it about — and a reader who has just read one wants the
          other. Timeline and Identity are the slower-moving facts and follow.

          The phase goes in so each row can show GROVE'S progress on that ticket
          beside the tracker's own status. It is the same object the Task card
          above already renders, handed down rather than fetched — both halves of
          the join are on this tab, so the card gains no request. */}
      <TicketRefsCard
        repoRoot={repoRoot}
        refs={state.ticket_refs}
        phase={activity?.phase ?? null}
        onNavigate={onNavigate}
      />

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
            {/* The record's stamps are passed IN rather than read off an
                embedded workspace state, because the public payload carries
                the two objects separately — see `newestActivityIso`. Same
                rule, same fallback order, one implementation. */}
            <PreciseAge
              iso={
                activity
                  ? newestActivityIso(
                      activity.sessions,
                      state.updated_at,
                      state.created_at,
                    )
                  : state.updated_at
              }
            />
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
              <CardField label={<Explain term="tool_time" />}>
                {duration(clocks.tool_ms)}
              </CardField>
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
          {/* Owned-stream facts: only a native session has them, and each
              renders only once its stream has said it — the absent-is-not-a-
              value rule the clocks above follow, applied per field. TTFT sits
              beside latency because it is the same question asked of the
              last call rather than averaged. */}
          {native?.ttft_ms != null && (
            <CardField label={<Explain term="native_ttft" />}>
              <span data-testid="native-ttft">{duration(native.ttft_ms)}</span>
            </CardField>
          )}
          {native?.cost_usd != null && (
            <CardField label={<Explain term="native_cost" />}>
              <span data-testid="native-cost">{USD.format(native.cost_usd)}</span>
            </CardField>
          )}
          {native?.last_exit_code != null && (
            <CardField label={<Explain term="native_exit_code" />}>
              <span
                data-testid="native-exit-code"
                className={cn(native.last_exit_code !== 0 && "text-destructive")}
              >
                {native.last_exit_code}
              </span>
            </CardField>
          )}
          {native?.subagents_spawned != null && (
            <CardField label="Subagents (session total)">
              <span className="tabular-nums" data-testid="native-subagent-census">
                {native.subagents_spawned} spawned
                {native.subagents_completed != null ? ` · ${native.subagents_completed} completed` : ""}
                {native.subagents_failed != null ? ` · ${native.subagents_failed} failed` : ""}
              </span>
            </CardField>
          )}
        </CardFields>
      </SectionCard>

      {/* Owner-only, and gated on `identity` for a reason the other cards are
          not: this card's read SPAWNS A SUBPROCESS daemon-side, so a public
          reader must never be able to trigger one. The card itself renders
          nothing at all unless the agent's own control surface answered — see
          its docstring on why absence is silence here rather than a row of
          "not measured". */}

      {/* LAST, and only for an owner. The machinery a workspace is made of is
          the least volatile thing on this tab and the thing a reader checks
          once, so it closes the read rather than interrupting it. */}
      {identity && <WorkspaceIdentityCard state={identity} />}
    </CardGrid>
  );
}
