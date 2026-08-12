"use client";

import type { ReactNode } from "react";
import { ChevronRightIcon } from "lucide-react";

import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

/**
 * What a card is, in this app — the single definition every surface composes.
 *
 * It lives here rather than in `workspace/` because three feature modules had
 * each grown their own: a work-panel card, a `UsageSection`, and the fleet's
 * hand-composed summary, all with different densities, type sizes and gutters.
 * Same reason `agent-mark.tsx` sits at this level: a thing two feature modules
 * both need must not live inside one of them.
 *
 * The scale, applied everywhere and nowhere overridden:
 *   card title  `text-sm font-medium`     · body       `text-sm`
 *   secondary   `text-xs text-content-tertiary`
 *   identifiers `font-mono text-xs`       · numbers add `tabular-nums`
 *
 * The spacing scale is equally short: `p-3` inside a card body, `gap-2` between
 * a body's own rows, `gap-3` between cards. Three values, no others.
 *
 * These compose; they do not configure. If a card needs to look different, the
 * answer is a different composition of `CardShell`, not another prop here — a
 * primitive with eight booleans is worse than the three systems it replaced.
 */

/**
 * The container, and the only place radius and elevation enter Grove code.
 *
 * Both come from the vendored `Card`; what this adds is the reset that every
 * Grove card wants — the vendored `gap-6 py-6` rhythm is built for a marketing
 * page, and a dashboard of twelve cards at that density is mostly padding. Its
 * children own their own padding instead.
 *
 * `overflow-hidden` is load-bearing, not defensive: a header band or a divided
 * list would otherwise paint square corners over the card's rounded ones.
 *
 * `shrink-0` PAYS FOR THAT `overflow-hidden`, and the pairing is the whole
 * point. A flex item's automatic minimum size is normally its content, which is
 * what stops a column squashing its children — but `overflow: hidden` resolves
 * `min-height: auto` to ZERO, so the clip that keeps the corners round also
 * hands the parent permission to crush the card to nothing. In an
 * `overflow-y-auto` column the browser shrinks before it scrolls, and it takes
 * the shrink out of the TALLEST item first, so the failure lands on whichever
 * card has the most to say. That is how the usage page's 365-day heatmap came
 * to render 365 correct cells inside a card two pixels tall — present in the
 * DOM, perfect in the inspector, and invisible on screen.
 *
 * A card is never shorter than its content. If the space is short, an ancestor
 * scrolls; the card does not silently eat the difference. Cards live in grids
 * and columns here, and a grid item ignores `flex-shrink` entirely, so this
 * costs nothing where it does not apply.
 */
export function CardShell({ className, ...props }: React.ComponentProps<typeof Card>) {
  return <Card className={cn("min-w-0 shrink-0 gap-0 overflow-hidden py-0", className)} {...props} />;
}

/**
 * A titled section: a header band that names it, and a body that answers it.
 *
 * The boundary is drawn twice on purpose — a tint plus a rule — because a card
 * whose header is only "the first line of the body" forces the reader to parse
 * prose to find where the section starts.
 *
 * `icon` and `title` take nodes rather than a component and a string so a card
 * whose identity is a link or a brand mark can use this instead of forking it;
 * the icon's size and colour stay owned here, so passing a node cannot drift.
 */
export function SectionCard({
  icon,
  title,
  description,
  action,
  flush = false,
  children,
  className,
  ...props
}: Omit<React.ComponentProps<"div">, "title"> & {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  /**
   * Drops the body's gutter for a child that already draws its own padded
   * surface — several vendored elements are self-framing, and stacking this
   * card's padding on theirs strands a double inset in a panel that is often
   * half a window wide.
   */
  flush?: boolean;
}) {
  return (
    <CardShell className={className} {...props}>
      <CardHeader className="gap-0.5 bg-muted/40 px-3 py-2.5">
        <CardTitle className="flex min-w-0 items-center gap-2 text-sm leading-5 font-medium">
          {icon && (
            <span className="flex size-4 shrink-0 items-center justify-center text-content-tertiary [&_svg]:size-4">
              {icon}
            </span>
          )}
          <span className="min-w-0 truncate">{title}</span>
        </CardTitle>
        {description && (
          <CardDescription className="min-w-0 text-xs leading-4">{description}</CardDescription>
        )}
        {action && <CardAction className="self-center">{action}</CardAction>}
      </CardHeader>
      {/* `flex-1` so the body FILLS its card rather than sitting at its natural
          height with dead space under it. It matters wherever two cards share a
          grid row: the row stretches both to the taller one, and without this
          the shorter card's content stopped where its content stopped, leaving
          the gap the user sees. Cards whose content is genuinely short are
          unaffected — a taller content box with the same children looks
          identical, because the only chrome here is the top rule. */}
      <CardContent
        className={cn(
          "flex min-w-0 flex-1 flex-col gap-2 border-t border-border text-sm",
          flush ? "p-0" : "p-3",
        )}
      >
        {children}
      </CardContent>
    </CardShell>
  );
}

/**
 * A row that hides its detail until asked — the shape three surfaces had each
 * open-coded: the transcript's file edits, the composer's plan, and the Files
 * tab's diffs.
 *
 * Collapse hides the DETAIL, never the SIGNAL: `summary` stays visible in both
 * states, so scanning still answers "what is this and how much moved" without a
 * click.
 *
 * Content mounts only while open, which is the whole point — an expanded
 * unified diff is thousands of DOM nodes, and rendering every one on mount is
 * what froze both surfaces. Radix already unmounts closed content; the explicit
 * `open &&` here states the requirement where a reader can see it, so a later
 * `forceMount` for an exit animation cannot silently restore the cost.
 *
 * Controlled rather than self-owning its state: every caller needs `open` for
 * something else — a `data-collapsed` attribute, or a count shown only while
 * collapsed — and handing it back out would be the same state twice.
 *
 * `header` draws the trigger with `SectionCard`'s own boundary — a tint plus a
 * rule — rather than a plain row, for the disclosure that IS a card's whole
 * surface: the trigger names the CARD, so an expanded card should read as
 * header-then-body the same way a `SectionCard` does, not as one undivided
 * block with a chevron in it. It is OFF by default because a `CardDisclosure`
 * is just as often a ROW inside an already-headed list — the Files tab's
 * per-file rows sit inside one `SectionCard`'s own tinted band, and a second
 * tint per row would compete with it rather than read as structure. Every
 * existing call site was checked deliberately, not defaulted past:
 *   - `workspace/todo-panel.tsx`, `workspace/queue-panel.tsx` — `header`. The
 *     disclosure IS the whole card (`CardShell` wraps only it), same shape as
 *     `SectionCard`'s title-bar-for-an-object case.
 *   - `workspace/files-tab.tsx`'s `FileDiffRow` — no `header`. A row in a
 *     `SectionCard`'s own `divide-y` list, not a card of its own.
 *   - `workspace/data-parts.tsx`'s `NotificationPart`, `workspace/file-edit-
 *     part.tsx`'s `FileEditPart` — no `header`, left as-is. Both wrap only a
 *     `CardDisclosure` in a `CardShell`, the same shape as the plan and queue
 *     cards, so they are plausible next candidates — but they sit in files
 *     this change does not own, so the decision here is explicitly deferred
 *     rather than applied silently.
 */
export function CardDisclosure({
  open,
  onOpenChange,
  summary,
  children,
  className,
  contentClassName,
  header = false,
  ...props
}: Omit<React.ComponentProps<typeof Collapsible>, "children"> & {
  summary: ReactNode;
  children: ReactNode;
  contentClassName?: string;
  header?: boolean;
}) {
  return (
    <Collapsible open={open} onOpenChange={onOpenChange} className={className} {...props}>
      <CollapsibleTrigger
        className={cn(
          "flex w-full min-w-0 items-center gap-2 px-3 py-2 text-start transition-colors hover:bg-muted/50",
          header && "bg-muted/40",
        )}
      >
        <ChevronRightIcon
          aria-hidden
          className={cn(
            "size-3.5 shrink-0 text-content-tertiary transition-transform",
            open && "rotate-90",
          )}
        />
        {summary}
      </CollapsibleTrigger>
      <CollapsibleContent className={cn(header && "border-t border-border", contentClassName)}>
        {open && children}
      </CollapsibleContent>
    </Collapsible>
  );
}

/**
 * A surface's canvas: a bounded column of cards that scrolls INSIDE its pane
 * and never grows the page.
 *
 * `min-h-0` is the load-bearing part — without it a flex child with
 * `overflow-y-auto` refuses to shrink below its content and the scroll silently
 * moves to the document. `auto-rows-min content-start` is the other half: a
 * grid row defaults to stretching, which is what made short cards balloon.
 *
 * `@container` scopes the responsive breakpoints to the PANE, not the viewport
 * — the work panel is half a window in split view, so a viewport-based
 * `md:grid-cols-2` would put two cards side by side in a column too narrow for
 * either.
 */
export function CardGrid({ children, className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "@container grid min-h-0 flex-1 auto-rows-min content-start gap-3",
        "overflow-y-auto bg-background p-3",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/**
 * A list long enough to need its own scroll, bounded so the card cannot become
 * the page.
 *
 * Native overflow rather than the vendored `ScrollArea`: radix lays its
 * viewport content out as a table, which breaks the `min-w-0` truncation every
 * row in these cards relies on. One scroll idiom per surface.
 */
export function CardScroll({ children, className, ...props }: React.ComponentProps<"div">) {
  return (
    <div className={cn("max-h-64 min-w-0 overflow-y-auto", className)} {...props}>
      {children}
    </div>
  );
}

/**
 * A labelled number. The label sits UNDER the value so a row of them reads as
 * one baseline of figures rather than a paragraph of words.
 *
 * SANS, NOT MONO. Mono means "a literal you could retype and have it mean the
 * same thing" — a path, a ref, an id. Everything that reaches here is a
 * QUANTITY: commits ahead, dirty files, `+12 −4`, turns, tool calls, tokens.
 * The alignment those need is `tabular-nums`, which is doing the whole job the
 * monospace face was credited with, and the usage page already renders the same
 * class of data in sans. This one line is why the audit map described the same
 * defect five times, once per work-panel tab — none of them styles its own
 * figures, they all come through here.
 *
 * `tone` survives §4.7 because the label sits directly under the value: `+12`
 * in green is a coloured figure with the word "added" beneath it, so the colour
 * is the second carrier rather than the only one.
 */
export function CardStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: ReactNode;
  /** `positive`/`negative` colour the VALUE only; the label stays muted. */
  tone?: "positive" | "negative";
}) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5" data-testid={`panel-stat-${label}`}>
      <span
        className={cn(
          "text-sm tabular-nums",
          tone === "positive" && "text-success",
          tone === "negative" && "text-destructive",
        )}
      >
        {value}
      </span>
      <span className="truncate text-xs text-content-tertiary">{label}</span>
    </div>
  );
}

/**
 * Term-and-value rows. The term column sizes to the widest term rather than to
 * a guess, so a card of these lines up without anyone picking a pixel width.
 */
export function CardFields({ children, className, ...props }: React.ComponentProps<"dl">) {
  return (
    <dl
      className={cn("grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-3 gap-y-1", className)}
      {...props}
    >
      {children}
    </dl>
  );
}

/**
 * One `CardFields` row.
 *
 * `mono` means IDENTIFIER, and only that — a literal you would retype: a ref, a
 * SHA, a path, a session id. It used to be documented as "identifiers **and**
 * timestamps", which is two jobs welded together, and the weld was `font-mono
 * tabular-nums` as a single unit. They are not one thing: mono is a claim about
 * what the value IS, and tabular figures are a claim about how digits should
 * line up. A timestamp wants the second and not the first.
 *
 * So `tabular-nums` moved out of the flag and onto every row. It is what §3
 * asks for on any figure, it costs nothing on prose, and it is redundant under
 * `font-mono`, whose digits are fixed-width already — which is the tell that
 * the two were never the same decision.
 *
 * `label` is a NODE rather than a string so a row can name its value with an
 * `<Explain>` — Grove's vocabulary is explained where it is used, and a term
 * like "compute time" appears as a field label far more often than as prose.
 * Widening it is not an invitation to compose a label: it stays one short noun
 * phrase, and anything that wants a second line wants a different component.
 */
export function CardField({
  label,
  children,
  mono = false,
}: {
  label: ReactNode;
  children: ReactNode;
  /** The value is an identifier — something retyping character-for-character
   * would matter for. Not for timestamps, durations or counts. */
  mono?: boolean;
}) {
  return (
    <>
      <dt className="text-xs text-content-tertiary">{label}</dt>
      <dd className={cn("min-w-0 truncate text-xs tabular-nums", mono && "font-mono")}>
        {children}
      </dd>
    </>
  );
}
