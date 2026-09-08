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
      {/* Description lives in the body, so the vendor's two-row grid would
          leave the action misaligned. Title and action share one flex row. */}
      <CardHeader className="surface-header flex min-h-8 items-center justify-between gap-2 px-3 py-1.5">
        <CardTitle className="flex min-w-0 items-center gap-2 text-sm leading-5 font-medium">
          {icon && (
            <span className="flex size-4 shrink-0 items-center justify-center text-content-tertiary [&_svg]:size-4">
              {icon}
            </span>
          )}
          <span className="min-w-0 truncate">{title}</span>
        </CardTitle>
        {action && <CardAction className="flex shrink-0 items-center self-center">{action}</CardAction>}
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
        {description && (
          <CardDescription className={cn("min-w-0 text-xs text-content-tertiary", flush && "px-3 pt-3")}>
            {description}
          </CardDescription>
        )}
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
 *   - `workspace/file-edit-part.tsx` — `header`. Native diff lines form its
 *     body directly, without another viewer header or card.
 *   - `workspace/data-parts.tsx`'s `NotificationPart` — no `header`.
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
 * A NAMED REGION INSIDE A CARD BODY: one bounded enclosure around facts that
 * belong together, so a reader sees where one thought ends without a rule
 * across the card or a heading above it.
 *
 * `CardShell`, not a `div` with a border, and that is the whole reason this
 * exists rather than being open-coded per surface: `CardShell` is the one place
 * radius and elevation enter Grove code. `card-region` scopes its container
 * radius token to the inner-cell role without overriding the vendored class.
 * `bg-muted/30` lifts it off the darker base body in either theme. This wash is
 * deliberately not claimed as a full ladder rung; the border groups the facts
 * even where the alpha's lightness step is small.
 *
 * A REGION IS AN ENCLOSURE, NEVER A CROP. It has no height of its own, so the
 * prose inside it wraps to whatever it needs; the bound is the perimeter, not a
 * `max-h-*`. A region whose data is absent is not rendered at all — an empty
 * enclosure claims a fact was measured and came back empty.
 */
export function CardRegion({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <CardShell
      className={cn("card-region flex min-w-0 flex-col gap-1.5 bg-muted/30 p-2.5", className)}
      {...props}
    />
  );
}

/**
 * ONE BOUNDED FACT: a label with its glyph on top, the figure beneath, and a
 * perimeter that groups the pair.
 *
 * The geometry is the contract, not the decoration. `min-h-16` is a REFERENCE
 * height rather than a fixed one — a row of these lines up at default scale, and
 * a cell whose label wraps under 200% zoom or a longer translation grows instead
 * of clipping. 10px inside, a 16px glyph 4px from a 12px/16px label, a 20px/26px
 * figure: 10 + 16 + 2 + 26 + 10 lands on 64.
 *
 * A CELL IS NOT A BUTTON. No pointer, no hover elevation, no tab stop — the
 * perimeter groups a label and a value, and a reader who cannot click it must
 * never be told otherwise. Where a term genuinely needs defining, the definition
 * rides the LABEL through `Explain`'s dotted affordance, so at most the few
 * cells with real vocabulary gain a stop rather than all of them.
 *
 * `derived` is §3's provenance rule applied to a figure Grove computed or summed
 * rather than read off a provider — a dashed underline on the VALUE, with the
 * `title` saying what it was computed from. Dashed, never dotted: dotted is the
 * glossary affordance on the label beside it, and two claims sharing one
 * decoration is two claims nobody can read.
 *
 * `muted` is for an absence — `Not measured` — which renders quieter and smaller
 * than a real figure so a missing class can never be mistaken for a small one. A
 * genuine zero is a figure and takes the ordinary treatment.
 */
export function CardCell({
  icon,
  label,
  value,
  title,
  tone,
  derived = false,
  muted = false,
  ...props
}: Omit<React.ComponentProps<"div">, "title"> & {
  icon?: ReactNode;
  label: ReactNode;
  value: ReactNode;
  /** What the figure was computed from, and its exact value where abbreviated. */
  title?: string;
  /**
   * The figure's semantic tone: the two the fleet's counters spend on these
   * exact numbers, plus the warning rung its dirty count takes. It survives
   * §4.7 because the label sits directly above the value and the sign is on the
   * figure, so the colour is the second carrier and never the only one. A
   * caller passing a tone for a ZERO would be claiming a change that did not
   * happen — the selector withholds it, not this component.
   */
  tone?: "added" | "removed" | "pending";
  derived?: boolean;
  muted?: boolean;
}) {
  return (
    <CardShell
      className="card-region flex min-h-16 min-w-0 flex-col justify-between gap-0.5 bg-muted/30 p-2.5"
      {...props}
    >
      {/* THE LABEL WRAPS; IT DOES NOT TRUNCATE. `Output tokens` at 12px needs
          about 84px and a cell in a 300px panel gives it 81, so truncation was
          not a rare edge — it was the ordinary narrow reading, and
          `Output toke…` is a label a reader has to guess at. The cell has a
          MINIMUM height rather than a fixed one precisely so it can absorb a
          second line, and grid siblings stretch together, so a row stays
          aligned. `items-start` keeps the glyph on the label's first line. */}
      <span className="flex min-w-0 items-start gap-1 text-xs leading-4 text-content-tertiary">
        {icon && (
          <span className="flex size-4 shrink-0 items-center justify-center [&_svg]:size-4">
            {icon}
          </span>
        )}
        <span className="min-w-0">{label}</span>
      </span>
      <span
        title={title}
        className={cn(
          "min-w-0 truncate tabular-nums",
          // `text-2xl`, NOT `text-xl` — this app rebased the whole ramp one
          // step down (`app/globals.css`), so its `--text-2xl` is exactly the
          // 20px/26px the metric figure asks for and `--text-xl` is 18px/24px.
          // Reading a Tailwind default off memory is how a figure ships a size
          // smaller than the one it was specified at, with nothing failing.
          muted
            ? "text-xs leading-4 text-content-tertiary"
            : cn("text-2xl", tone ? CELL_TONE[tone] : "text-content-primary"),
          derived && "underline decoration-dashed underline-offset-2",
        )}
        data-derived={derived || undefined}
      >
        {value}
      </span>
    </CardShell>
  );
}

/**
 * One tone table, never a call-site choice (§4.1) — and the same three the
 * fleet's counters already spend on these exact figures, so one number renders
 * one way wherever it appears.
 */
const CELL_TONE = {
  added: "text-success",
  removed: "text-destructive",
  pending: "text-warning",
} as const;

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
