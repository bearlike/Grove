"use client";

import { TicketIcon, TriangleAlertIcon } from "lucide-react";

import { SectionCard } from "@/components/grove/card";
import { PhaseBadge } from "@/components/grove/fleet/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { PhaseView, TicketProviderView, TicketRef } from "@/lib/grove/api";
import { ticketKey, useTicketProviders, useTickets } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import {
  mergeTicket,
  providerLabel,
  sortTicketRefs,
  ticketGlyph,
  ticketIdLabel,
  ticketKindLabel,
  ticketPhaseKey,
  ticketPhases,
  ticketState,
  ticketStateColour,
  ticketStatusTone,
  titleRuns,
  type TicketPhaseMark,
} from "./selectors";

/**
 * The issues and pull requests this workspace is working — the tracker's view of
 * the job, beside the agent's.
 *
 * WHY THIS IS A CARD AND NOT A ROW OF CHIPS. The linkage used to render as
 * `#123` badges inside a "Links" card that was mounted only when
 * `ticket_refs` was non-empty — so a workspace with no ticket showed NOTHING:
 * not the card, not an explanation, not the command that would attach one. The
 * two most common states of this data (empty, and "one issue whose title you
 * want to read") were the two it served worst. The card is now unconditional and
 * every state below is a state a reader can act on.
 *
 * Mounts on an issue ALONE. An orchestrator attaches the issue at create and
 * the PR appears near the end, so requiring both would blank the linkage for
 * most of a workspace's life.
 *
 * PROVIDER-NEUTRAL BY CONSTRUCTION: nothing here knows what a Gitea is. Every
 * tracker-specific string comes from the wire (`provider`, `status`, `url`) or
 * from a `Record` over the wire enum in `selectors.ts`, so a fourth provider is
 * a row in that table and no change here.
 *
 * TWO CLAIMS PER ROW, AND THE POINT IS THAT THEY CAN DISAGREE. The tracker says
 * what the ticket IS (`open`, `merged`) and the agent says how far GROVE has got
 * on it — an issue that still reads `open` while Grove reports `delivering` is
 * the ordinary shape of a task in flight, and a reader who can only see one half
 * cannot tell that from a workspace that has stalled. `phase` is threaded in
 * rather than fetched: the Info tab already holds both halves, so the join is a
 * pure `Map` lookup and this card gains no request, no hook and no failure mode.
 */
export function TicketRefsCard({
  repoRoot,
  refs,
  phase,
}: {
  repoRoot: string;
  refs: readonly TicketRef[];
  phase: PhaseView | null;
}) {
  const providers = useTicketProviders(repoRoot);
  const configured = configuredProviders(providers.data);
  const live = useTickets(repoRoot, refs, configured);
  const claims = ticketPhases(phase);

  // The stored refs are the spine and the live reads are enrichment over them:
  // rows exist, in order, before any request resolves.
  //
  // `providers.isPending` counts as resolving. Nothing can be asked until the
  // provider list lands, and the first paint that treated "not asked yet" as
  // "answered nothing" said `No title recorded` about a ticket whose title
  // arrived 200ms later — an unknown rendered as a fact.
  const rows = sortTicketRefs(refs).map((ref) => ({
    // The STORED coordinate, not the merged one: a resolve may correct `kind`
    // (a forge numbers issues and pull requests in one space, so a ref attached
    // as an issue can come back a PR), and keying a row on a value the server
    // can change is how two rows collide on one React key.
    key: ticketKey(ref),
    ticket: mergeTicket(ref, live.byKey.get(ticketKey(ref))),
    // Keyed off the STORED coordinate for the same reason `key` is, and one
    // more: `ticketPhaseKey` omits `kind` entirely, so the claim stays attached
    // even across the correction that would move a three-part key.
    //
    // `?? null` is the whole "not reported" state. A ticket the agent has not
    // claimed gets no mark — never `scoping`, never an unfilled step zero —
    // which is exactly how the fleet already draws a workspace that has
    // reported nothing.
    phase: claims.get(ticketPhaseKey(ref)) ?? null,
    resolving: (providers.isPending || live.loading) && !live.byKey.has(ticketKey(ref)),
  }));

  // Only claimable once the provider list has actually ANSWERED. Computed off
  // `providers.data ?? []` while it was loading, this note accused every
  // provider on the card — including the configured one — of having no
  // credentials, for the whole first paint. A check that has not run yet has no
  // verdict to report.
  const unresolvable = providers.isSuccess
    ? [...new Set(refs.map((ref) => ref.provider))].filter(
        (provider) => !configured.includes(provider),
      )
    : [];

  return (
    <SectionCard
      icon={<TicketIcon />}
      title="Tickets"
      description="Issues and pull requests this workspace is tracking"
      action={
        refs.length > 0 ? (
          <Badge variant="outline" className="tabular-nums" data-testid="ticket-count">
            {refs.length} linked
          </Badge>
        ) : undefined
      }
      flush
      className="@xl:col-span-2"
      data-testid="tickets-card"
    >
      {/* One child, because a `flush` card's body still spaces its rows: the
          list and its notes are one stack separated by rules, not by gutters. */}
      <div className="flex min-w-0 flex-col">
        {rows.length === 0 ? (
          <TicketsEmpty providers={providers.data} failed={providers.isError} />
        ) : (
          <ul className="divide-y divide-border" data-testid="ticket-refs">
            {rows.map(({ key, ticket, resolving, phase: claim }) => (
              <li key={key}>
                <TicketRow ticket={ticket} resolving={resolving} phase={claim} />
              </li>
            ))}
          </ul>
        )}

        {rows.some(({ ticket }) => ticket.ambiguous) && (
          <Note data-testid="tickets-ambiguous-note">
            A link marked <span className="text-content-secondary">uncertain</span> was inferred
            from this branch, and more than one ticket matched. Confirm it with{" "}
            <Command>grove tickets attach</Command>, or drop it with{" "}
            <Command>grove tickets detach</Command>.
          </Note>
        )}

        {/* Scoped to the field that is actually missing: the rows are real, only
            their freshness is not. Naming the provider is what makes the message
            actionable — the fix is a token for THAT tracker. */}
        {rows.length > 0 && unresolvable.length > 0 && (
          <Note data-testid="tickets-degraded">
            Showing what Grove last recorded.{" "}
            {unresolvable.map((provider) => providerLabel(provider)).join(", ")}{" "}
            {unresolvable.length === 1 ? "has" : "have"} no credentials configured here, so live
            status cannot be read.
          </Note>
        )}

        {/* A different failure from a failed resolve, and a different sentence:
            nothing was asked, because the list of who to ask never arrived. */}
        {rows.length > 0 && providers.isError && (
          <Note data-testid="tickets-providers-failed">
            <span className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="text-destructive">
                Could not read this project&apos;s ticket providers.
              </span>
              <span>Every row is the last value Grove recorded.</span>
              <Button variant="outline" size="sm" onClick={() => void providers.refetch()}>
                Retry
              </Button>
            </span>
          </Note>
        )}

        {live.failed > 0 && (
          <Note data-testid="tickets-error">
            <span className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="text-destructive">
                {live.failed === 1 ? "One ticket" : `${live.failed} tickets`} could not be
                refreshed.
              </span>
              <span>Every row above is the last value Grove recorded.</span>
              <Button variant="outline" size="sm" onClick={live.retry}>
                Retry
              </Button>
            </span>
          </Note>
        )}
      </div>
    </SectionCard>
  );
}

/**
 * One ticket, as a row you can read rather than a chip you can only click.
 *
 * The whole row is the link when there is a URL — a bigger target than an id,
 * and the hover feedback covers everything the click would take you to. With no
 * URL it is a plain row rather than a dead link: a ref can be attached before
 * its tracker was ever reachable, and a link that goes nowhere is worse than
 * text that never claimed to.
 *
 * `phase` DEFAULTS TO NULL because that is a real state, not a missing argument:
 * a workspace whose agent reports one overall phase without naming tickets, and
 * an older daemon that sends no per-ticket rows at all, both land here — and
 * both must leave the row looking exactly as it did before this axis existed.
 */
export function TicketRow({
  ticket,
  resolving,
  phase = null,
}: {
  ticket: TicketRef;
  resolving: boolean;
  phase?: TicketPhaseMark | null;
}) {
  const state = ticketState(ticket.status);
  const KindIcon = ticketGlyph(ticket.kind, state);
  const kind = ticketKindLabel(ticket.kind);
  const meta = [providerLabel(ticket.provider), kind, ticket.assignee ? `@${ticket.assignee}` : ""]
    .filter(Boolean)
    .join(" · ");

  const body = (
    <>
      {/* The forge convention, and the reason it works without being taught:
          the SHAPE says issue-or-PR and open-or-merged-or-closed, and the hue
          only agrees with it. Read in greyscale the row loses nothing. */}
      <KindIcon
        aria-hidden
        className={cn("mt-0.5 size-[1em] shrink-0", ticketStateColour(state))}
        data-testid="ticket-glyph"
        data-state={state}
      />
      <span className="sr-only">
        {kind}, {state}:{" "}
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex min-w-0 items-baseline gap-2">
          {/* Dotted underline: the whole row is the link, but the id is the
              part a reader points at, so it is the part that has to look like
              a destination rather than a label. */}
          <span className="shrink-0 font-mono text-xs tabular-nums text-content-primary underline decoration-dotted underline-offset-2">
            {ticketIdLabel(ticket)}
          </span>
          {resolving && !ticket.title ? (
            // A skeleton only where there is genuinely nothing to show. A title
            // Grove already holds is never replaced by a loading shape — stale
            // text beats a spinner over information the reader can already use.
            <Skeleton className="h-4 w-40" data-testid="ticket-title-loading" />
          ) : (
            <span className="min-w-0 flex-1 truncate text-sm text-content-primary">
              {ticket.title ? (
                <TicketTitle title={ticket.title} />
              ) : (
                <span className="text-content-tertiary">No title recorded</span>
              )}
            </span>
          )}
        </span>
        <span className="truncate text-xs text-content-tertiary">{meta}</span>
      </span>
      {/* THE BADGE BUDGET ON THIS ROW, WORKED OUT RATHER THAN ASSUMED (§6).
          Four marks can land here now — an uncertain link, the tracker's own
          status, Grove's phase, and blocked — but at most two of them are ever
          TONED, so the row keeps a step of headroom under the three-toned cap.

          - `uncertain` is `outline`. It is a fact about the LINK, not a state
            of the ticket, and a hairline chip is what §6 gives a neutral fact.
          - The tracker's status comes from `STATUS_TONE`: `outline` or
            `secondary`, never `default`, decided when the glyph took the hue.
          - Grove's phase is `outline` while the work is moving. It reports a
            POSITION, and a position must never out-shout the ticket's own word.
          - BLOCKED IS THE ONE MARK THAT WINS, and it wins by being the row's
            only `destructive`. It is the only fact here anybody can act on
            right now, and it is a claim about GROVE'S work — which is why it
            rides the phase badge rather than arriving as a fifth chip.

          NOTHING on this card is `default`. The one loudest mark §6 allows this
          object is still spent where it always was: the workspace status badge
          on the Identity card beside this one.

          ORDER IS READING ORDER: what the ticket says, then what Grove says
          about it. The tracker's word stays adjacent to the tracker's content,
          and Grove's claim is the last column — the one a reader consults after
          they know what they are looking at. */}
      <span className="flex shrink-0 items-center gap-1.5">
        {ticket.ambiguous && (
          <Badge
            variant="outline"
            title="Grove inferred this link from the branch and more than one ticket matched — it may be the wrong one."
            data-testid="ticket-ambiguous"
          >
            <TriangleAlertIcon aria-hidden />
            uncertain
          </Badge>
        )}
        {ticket.status && (
          <Badge variant={ticketStatusTone(ticket.status)} data-testid="ticket-status">
            {ticket.status}
          </Badge>
        )}
        {/* The ticket goes in so the hover can state the TRACKER'S claim beside
            Grove's own, attributed to each. The two axes are the thing readers
            conflate, and a mark that explains only its own half invites it. */}
        <PhaseBadge phase={phase} ticket={ticket} />
      </span>
    </>
  );

  // The full title lives in `title` on the row, so a name clipped in a docked
  // half-width panel is still recoverable without opening the tracker.
  const shared = "flex w-full min-w-0 items-start gap-2 px-3 py-2 text-start";
  if (!ticket.url) {
    return (
      <div className={shared} title={ticket.title ?? undefined}>
        {body}
      </div>
    );
  }
  return (
    <a
      href={ticket.url}
      target="_blank"
      rel="noreferrer"
      title={ticket.title ?? ticket.url}
      className={cn(
        shared,
        "transition-colors hover:bg-muted/50",
        // INSET, because the card clips: a row is the full width of a
        // `overflow-hidden` card, so an outside ring loses its left and right
        // edges and reads as two stray rules rather than as a focus mark.
        //
        // OPAQUE, because it is the ONLY layer. shadcn draws focus as two: an
        // opaque 1px `focus-visible:border-ring` that carries the contrast, plus
        // a `ring-ring/50` glow that is decoration. This row took `outline-none`
        // and kept only the glow, so the whole affordance was a 50%-alpha halo —
        // composited against the card that is 1.85:1 light and 1.87:1 dark,
        // under the 3:1 floor a state indicator owes (WCAG 1.4.11). A keyboard
        // user could not see where they were.
        //
        // MEASURE THE COMPOSITE, NOT THE TOKEN: `--ring` alone reads 4.05 / 3.67
        // and looks compliant, which is how this shipped. The alpha is the whole
        // defect and a contrast table that lists colours cannot see it.
        "focus-visible:inset-ring-2 focus-visible:inset-ring-ring focus-visible:outline-none",
      )}
    >
      {body}
    </a>
  );
}

/**
 * A ticket title, with its code spans drawn as code and everything else left
 * exactly as the tracker wrote it.
 *
 * INLINE ONLY, and structurally so: `titleRuns` can only ever produce spans, so
 * there is no configuration under which this emits a heading, a list or a block
 * and breaks the row. See that function for why the vendored markdown renderer
 * could not be used — it takes no text input at all.
 */
function TicketTitle({ title }: { title: string }) {
  return (
    <>
      {titleRuns(title).map((run, index) =>
        run.code ? (
          // `font-mono` and nothing else: a code span inside a truncating row
          // must not bring a background or a radius, which would clip against
          // the ellipsis and read as a rendering fault.
          <code key={index} className="font-mono">
            {run.text}
          </code>
        ) : (
          <span key={index}>{run.text}</span>
        ),
      )}
    </>
  );
}

/**
 * No ticket attached — the state a user is most likely to be looking at, and the
 * one the old chip row rendered as an absent card.
 *
 * It says what would be here, how to put one here, and which trackers this
 * project can even talk to. Quiet by the rule that an absence is never the
 * loudest thing on a screen: tertiary, no weight, no colour.
 */
function TicketsEmpty({
  providers,
  failed,
}: {
  providers: TicketProviderView[] | undefined;
  failed: boolean;
}) {
  return (
    <div
      className="flex flex-col items-center gap-2 px-3 py-6 text-center text-sm text-content-tertiary"
      data-testid="tickets-empty"
    >
      <p>No issue or pull request is linked to this workspace.</p>
      <p className="text-xs">
        Attach one with <Command>grove tickets attach &apos;#42&apos;</Command>, name a ticket when
        you create a workspace, or let Grove infer it from a branch that carries the id.
      </p>
      <TrackerLine providers={providers} failed={failed} />
    </div>
  );
}

/**
 * Which trackers this project is wired to — the difference between "you have
 * not linked anything yet" and "nothing here could be linked".
 */
function TrackerLine({
  providers,
  failed,
}: {
  providers: TicketProviderView[] | undefined;
  failed: boolean;
}) {
  if (failed) {
    return (
      <p className="text-xs" data-testid="tickets-providers-error">
        <span className="text-destructive">Could not read this project&apos;s trackers.</span> The
        daemon answered with an error; reopen this tab to try again.
      </p>
    );
  }
  if (providers === undefined) {
    return <Skeleton className="h-4 w-56" data-testid="tickets-providers-loading" />;
  }
  if (providers.length === 0) {
    return (
      <p className="text-xs">
        No ticket provider is enabled for this project — set one up under{" "}
        <Command>tickets</Command> in the config cascade.
      </p>
    );
  }
  return (
    <p className="text-xs" data-testid="tickets-providers">
      {providers.map((provider) => (
        <span key={provider.provider} className="mr-2 inline-flex items-center gap-1">
          {providerLabel(provider.provider)}
          {provider.context && <span className="font-mono">{provider.context}</span>}
          {!provider.configured && <span>(no credentials)</span>}
        </span>
      ))}
    </p>
  );
}

/** A note under the list: metadata about the list, never louder than a row. */
function Note({ children, ...props }: React.ComponentProps<"div">) {
  return (
    <div className="border-t border-border px-3 py-2 text-xs text-content-tertiary" {...props}>
      {children}
    </div>
  );
}

/** A literal you could retype — the one thing on this card that earns mono. */
function Command({ children }: { children: React.ReactNode }) {
  return <span className="font-mono text-content-secondary">{children}</span>;
}

/** Only a provider with credentials can answer, so only those are ever asked. */
function configuredProviders(
  providers: TicketProviderView[] | undefined,
): TicketRef["provider"][] {
  return (providers ?? []).filter((provider) => provider.configured).map((p) => p.provider);
}
