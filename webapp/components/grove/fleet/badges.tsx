import { TriangleAlertIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Explain } from "@/components/grove/glossary";
import {
  phaseTooltip,
  ticketGlyph,
  ticketKindLabel,
  ticketState,
  ticketStateColour,
  type PhaseTooltip,
  type PhaseTooltipTicket,
} from "@/components/grove/workspace/selectors";
import type { TicketRef } from "@/lib/grove/api";
import {
  agentAccent,
  agentGlossaryTerm,
  agentLabel,
  agentTone,
  phaseGlyph,
  progressAccent,
  runtimeGlossaryTerm,
  runtimeGlyph,
  runtimeLabel,
  statusAccent,
  statusGlossaryTerm,
  statusTone,
} from "./tokens";
import type { AgentState, Runtime, TaskPhase, TodoProgress, WorkspaceStatus } from "./types";

/**
 * The marks for Grove's three status axes plus runtime, each one a `Badge`.
 *
 * They exist as separate exports rather than one `<Marks workspace={…}/>`
 * because the surfaces disagree about which axes they have room for: a sidebar
 * row shows one, a card shows four.
 *
 * TONE AND ACCENT ARE TWO SEPARATE READS OF THE SAME TABLE, AND BOTH COME FROM
 * `tokens.ts`. The `variant` says how LOUD a mark is on the vendored badge's
 * own four-tone scale; the accent says WHICH of two hues the badge's scale
 * cannot name — work in flight, and work finished. Both are looked up, never
 * chosen here: §6's "a variant is never chosen at a call site" is a rule about
 * the decision, not about the prop, so an accent picked inline would break it
 * just as surely.
 *
 * The accent rides `className` because a vendored component leaves no other
 * seam, and it is a semantic-token class rather than a palette step, so the
 * theme still owns what it resolves to — the same standing `variant` has.
 */

/** Axis 1 — the workspace lifecycle, as the daemon sees it. */
export function StatusBadge({ status }: { status: WorkspaceStatus }): React.ReactNode {
  const term = statusGlossaryTerm(status);
  return (
    <Badge
      variant={statusTone(status)}
      className={statusAccent(status)}
      data-testid="status-badge"
      data-status={status}
    >
      {term ? <Explain term={term}>{status}</Explain> : status}
    </Badge>
  );
}

/** Axis 2 — what the agent is doing right now. */
export function AgentStateBadge({ state }: { state: AgentState }): React.ReactNode {
  const term = agentGlossaryTerm(state);
  const label = agentLabel(state);
  return (
    <Badge
      variant={agentTone(state)}
      className={agentAccent(state)}
      data-testid="agent-state-badge"
      data-state={state}
    >
      {term ? <Explain term={term}>{label}</Explain> : label}
    </Badge>
  );
}

/**
 * Everything a phase mark needs to draw itself — STRUCTURAL rather than the wire
 * type, because two different objects now carry a phase: a workspace (`PhaseView`
 * itself) and one of the tickets it is working (a row inside that view, widened
 * with its parent's `total`). Both satisfy this shape as they are; neither had to
 * be reshaped into the other, and no call site casts.
 */
export type PhaseMark = {
  phase: TaskPhase;
  note?: string | null;
  index: number;
  total: number;
  blocked: boolean;
};

/**
 * Axis 3 — the phase the agent reports for itself. Renders nothing when it has
 * never reported one: a workspace without a phase must look exactly as it did
 * before this axis existed, not like a workspace stuck at step zero.
 *
 * BLOCKED IS ORTHOGONAL TO THE PHASE, so it never moves the fraction: the badge
 * still reports the position reached and only the glyph and the tone say the
 * agent is not advancing from it. That is the one thing on a twenty-card wall
 * worth interrupting a scan for, so it takes `destructive` — the tone this app
 * spends on "a human is needed" — while an unblocked phase stays a quiet
 * `outline` neutral fact.
 *
 * The budget that permits it (§6): a card row can now show this `destructive`
 * beside a waiting agent's, which is two toned marks inside the three the row
 * allows. What §6 caps at one is `default`, and this axis has never claimed it —
 * the workspace status badge in the card header still owns the object's only
 * loudest mark.
 */
export function PhaseBadge({
  phase,
  ticket,
}: {
  phase: PhaseMark | null | undefined;
  /**
   * The ticket this mark belongs to, where it belongs to one. Absent on a FLEET
   * card, whose phase is the workspace's own and has no tracker to consult.
   */
  ticket?: PhaseTooltipTicket | null;
}): React.ReactNode {
  if (!phase) return null;
  const tip = phaseTooltip(phase, ticket);
  return (
    <Tooltip>
      {/*
        REPLACES the native `title` this badge used to carry rather than
        stacking on it — §3's "one figure, one hover affordance". A `title`
        cannot hold the four separate claims below, and a browser tooltip and a
        Radix one on the same element race each other.

        `TooltipTrigger asChild` over the `Badge` (rather than the vendored
        `TooltipIconButton`, whose `tooltip` prop is typed `string` and so can
        carry a sentence and nothing else) — the same composition
        `usage/window-meter.tsx` documents for structured content.

        `tabIndex={0}` because `Badge` is a plain `<span>`: without it the
        explanation is mouse-only, which excludes exactly the readers who most
        need a word they cannot decode. `usage/abbreviated-number.tsx` is the
        established compensation and this is the same one.
      */}
      <TooltipTrigger asChild>
        <Badge
          variant={phase.blocked ? "destructive" : "outline"}
          tabIndex={0}
          data-testid="phase-badge"
          data-phase={phase.phase}
          data-blocked={phase.blocked || undefined}
          aria-label={tip.aria}
        >
          <span aria-hidden>{phaseGlyph(phase.phase, phase.blocked)}</span>
          <span className="tabular-nums">
            {phase.index + 1}/{phase.total}
          </span>
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-72">
        <PhaseTooltipBody tip={tip} />
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * The hover's body, EXPORTED so it can be rendered on its own.
 *
 * Radix portals its content and mounts it only while open, so nothing inside a
 * `TooltipContent` exists in an SSR render — which is this suite's whole idiom.
 * A named export is the honest seam, the same one `WindowDetailRows` is.
 *
 * NO COLOUR AT ALL. This sits on `TooltipContent`'s inverted surface, where the
 * `--content-*` tiers are tuned against the wrong background (measured under AA
 * there — see `usage/window-meter.tsx`'s `Detail`). Rank comes from weight and
 * from order instead.
 */
export function PhaseTooltipBody({ tip }: { tip: PhaseTooltip }): React.ReactNode {
  return (
    <div className="flex flex-col gap-1" data-testid="phase-tooltip">
      <p className="font-medium" data-testid="phase-tooltip-headline">
        {tip.headline}
      </p>
      <p data-testid="phase-tooltip-meaning">{tip.meaning}</p>
      {tip.stall && <p data-testid="phase-tooltip-stall">{tip.stall}</p>}
      {/* Quoted and italic because it is SOMEBODY ELSE'S SENTENCE: the agent's
          own words, rendered exactly as written rather than folded into the
          voice of the sentences around it. */}
      {tip.note && (
        <p className="italic" data-testid="phase-tooltip-note">
          &ldquo;{tip.note}&rdquo;
        </p>
      )}
      {tip.tracker && <p data-testid="phase-tooltip-tracker">{tip.tracker}</p>}
    </div>
  );
}

/**
 * The isolation boundary. A fallback — the workspace asked for a container and
 * got the host — is marked for the workspace's whole lifetime, because its
 * isolation contract was voided and only a respawn can restore it.
 */
export function RuntimeBadge({
  runtime,
  fallbackReason,
}: {
  runtime: Runtime;
  fallbackReason?: string | null;
}): React.ReactNode {
  if (fallbackReason) {
    return (
      <Badge
        variant="outline"
        // The badge's own title carries the SPECIFIC reason — an instance
        // fact the glossary cannot hold, since one term has one fixed
        // sentence. `Explain` on the label carries the general concept
        // instead: two different questions, deliberately not merged into one
        // affordance.
        title={`Container unavailable: ${fallbackReason} — fix the runtime, then respawn`}
        data-testid="runtime-badge"
        data-runtime={runtime}
        data-fallback="true"
      >
        <TriangleAlertIcon aria-hidden />
        <Explain term="runtime_fallback">fallback</Explain>
      </Badge>
    );
  }

  const Icon = runtimeGlyph(runtime);
  return (
    <Badge variant="outline" data-testid="runtime-badge" data-runtime={runtime}>
      <Icon aria-hidden />
      <Explain term={runtimeGlossaryTerm(runtime)}>{runtimeLabel(runtime)}</Explain>
    </Badge>
  );
}

/**
 * The agent's own checklist, as a fraction — and the ONE quantity on this card
 * that earns a hue.
 *
 * §6 draws the line this sits on: an aggregate is a MAGNITUDE and a single
 * claim is a POSITION. `PhaseBadge` above reports a position, so it spends no
 * tone on how far along it is and says so in shape (`○ ◔ ◑ ◕ ● ✓`); this is a
 * completion over a batch, which is the same fact `in flight`/`done` names on
 * every other axis, counted instead of stated. So amber while it runs, green
 * when it is all in, and — via `progressAccent` — NOTHING at zero, because
 * colouring `0/6` would claim work had begun when none has.
 *
 * `outline` under the accent, not `secondary`: §6 sends a count to `outline`,
 * and keeping the table's variant means the hairline survives the accent, so a
 * toned chip still sits in the same set as the untoned ones beside it.
 *
 * The `☑` and the fraction carry the meaning with the hue removed (§4.7), which
 * is why the accent can be a fill here at all.
 */
export function TodoBadge({ todo }: { todo: TodoProgress }): React.ReactNode {
  return (
    <Badge
      variant="outline"
      className={progressAccent(todo.completed, todo.total)}
      title="todo checklist the agent is keeping"
      data-testid="todo-badge"
    >
      <span aria-hidden>☑</span>
      <span className="tabular-nums">
        {todo.completed}/{todo.total}
      </span>
      <span className="sr-only">todo items done</span>
    </Badge>
  );
}

/**
 * One attached ticket, small enough that a wall of twenty cards can carry five
 * of them without becoming a list.
 *
 * TWO FACTS, ONE CHIP, AND ONLY ONE OF THEM IS A STATE. Which tracker and which
 * number is the ticket's IDENTITY — fixed, so §6 forbids it a tone and it stays
 * neutral text. Whether the tracker calls it open, merged or closed is a state,
 * and it rides the GLYPH: `ticketGlyph` changes shape per kind and state and
 * `ticketStateColour` agrees with it in hue, which is §4.7's rule (shape first,
 * colour second) and the forge convention every developer already reads.
 *
 * The chip itself is therefore `outline` on every ticket, unlike the Info tab's
 * `TicketRow`, which spends `ticketStatusTone` on a badge holding the tracker's
 * own WORD. The two surfaces are not inconsistent: there the badge IS the state
 * mark, here the badge is the identity and the glyph is the state mark. What
 * that buys is the row's whole tone budget — a card with five tickets renders
 * five untoned chips, so §6's three-toned cap is never in play on a row whose
 * length the user controls.
 *
 * The label is `provider#id` rather than `ticketIdLabel`'s forge spelling
 * because `matchesQuery` searches on exactly this string: what a reader sees on
 * the chip is what they can paste into the fleet search and find.
 */
export function TicketChip({ ticket }: { ticket: TicketRef }): React.ReactNode {
  const state = ticketState(ticket.status);
  const Glyph = ticketGlyph(ticket.kind, state);
  const label = `${ticket.provider}#${ticket.id}`;
  const body = (
    <>
      <Glyph aria-hidden className={ticketStateColour(state)} />
      <span className="sr-only">
        {ticketKindLabel(ticket.kind)}, {state}:{" "}
      </span>
      {label}
    </>
  );

  // A ref can be attached before its tracker was ever reachable, so a chip with
  // no URL is a plain chip rather than a dead link — same call `TicketRow`
  // makes one layer down.
  return ticket.url ? (
    <Badge variant="outline" asChild data-testid="ticket-chip" data-state={state}>
      <a href={ticket.url} target="_blank" rel="noreferrer" title={ticket.title ?? label}>
        {body}
      </a>
    </Badge>
  ) : (
    <Badge
      variant="outline"
      title={ticket.title ?? label}
      data-testid="ticket-chip"
      data-state={state}
    >
      {body}
    </Badge>
  );
}

/**
 * The scanning signal for a large fleet: the one thing that turns twenty
 * workspaces into the two that want a human.
 *
 * ONLY FOR AGGREGATES — a count over many workspaces, never a mark on one. On a
 * single workspace this is `AgentStateBadge` restated: the daemon derives
 * `needs_attention` as `state ∈ {waiting, blocked, error}`, so a card rendering
 * both printed "waiting for you" and "needs you" side by side. The per-object
 * signal now rides `AGENT_TONE`, which keeps the loudness and gains the precise
 * word. Passing no `count` is what a single object would do — hence the
 * required parameter.
 */
export function AttentionBadge({ count }: { count: number }): React.ReactNode {
  return (
    <Badge variant="destructive" data-testid="attention-badge">
      {count} need you
    </Badge>
  );
}
