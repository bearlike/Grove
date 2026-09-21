"use client";

import * as React from "react";
import type { LucideIcon } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * THE STATUS BAND'S PRIMITIVES — the only place its structural decisions live.
 *
 * Every section of the band is composed from the five things here and nothing
 * else: a `Section` (ground + full-height rule), a run of `Groups` divided by
 * `Seam`s, a `Glyph` on the text baseline, and a `Value` that elides. A section
 * therefore cannot choose its own size, its own divider weight or its own
 * icon geometry, because it never sees those choices — consistency is a
 * property of the composition, not of every author remembering the rules.
 *
 * ONE SIZE. The band is `text-xs` on the `<footer>` and nothing here names a
 * size. Counted before this: 18 `text-sm` and 8 `text-xs` in one 24px strip,
 * each group setting its value in one size and its word in another. Chrome is
 * read at a glance and a glance cannot use two sizes; it can use TONE, so the
 * value/word distinction is `text-content-secondary` against
 * `text-content-tertiary`, the same two tiers every other chrome band uses.
 *
 * TWO DIVIDERS, ORDERED. `Section` closes with the loud `footer-rule` at the
 * band's full height; `Seam` is the quiet `footer-seam` at half of it, drawn
 * only BETWEEN groups by `Groups`. Inside a group the divider is whitespace —
 * a group is precisely the set of values that belong together, and a line
 * between two of them says the opposite. The colour arithmetic is beside the
 * tokens in `globals.css`.
 */

export type SectionId =
  | "footer-workspace"
  | "footer-git"
  | "footer-fleet"
  | "footer-subscriptions"
  | "footer-uptime"
  | "footer-system";

/**
 * One section of the band: its own ground, closed by a rule on the right.
 *
 * `wash` lifts the two MIDDLE sections so the outer two read as a pair;
 * `accent` is the ground the band's two ENDS share. Both are theme utilities
 * rather than colour classes, because `lint:styling` reserves colour for the
 * theme boundary and because this band sits on the darkest rung, where the
 * app's ordinary `--border` reads as undrawn.
 *
 * `overflow-hidden` IS THE LAST GUARD, AND IT IS THE SECTION'S TO MAKE. Value
 * ceilings bound what each string may claim, but a section also carries groups
 * that legitimately cannot shrink — a context reading, a runtime word — so a
 * narrow enough band still sums past the section's width. Measured at 1024px
 * before this: the workspace section ended at 407px with its context figure
 * drawn to 414px, i.e. 6px THROUGH its own closing rule and into the git
 * section's first value. A section that paints over its neighbour is the
 * defect the rule between them exists to deny, and no per-value ceiling can
 * express "and never past here" — only the box can.
 */
export function Section({
  children,
  id,
  wash = false,
  accent = false,
  className = "",
}: {
  children: React.ReactNode;
  id: SectionId;
  wash?: boolean;
  accent?: boolean;
  className?: string;
}): React.ReactNode {
  const ground = accent ? "footer-accent" : wash ? "footer-wash" : "";
  return (
    <section
      // `gap-0`, with air supplied per group by `Groups`: a seam has to sit
      // between two groups with equal space on each side, and a container gap
      // would stack on top of that and make the rule read as belonging to
      // whichever neighbour the browser rounded toward.
      className={`footer-rule flex h-full min-w-0 items-center gap-0 overflow-hidden border-r px-1 last:border-r-0 ${ground} ${className}`}
      data-testid={id}
    >
      {children}
    </section>
  );
}

/**
 * The divider BETWEEN two groups — the quiet tier.
 *
 * Its height is a rendered pixel set beside the band's own in `globals.css`
 * (`h-3` measured 9.59px at the 80% density root, so the half-band ratio
 * drifted with a lever that leaves the band alone). `aria-hidden`: every
 * value beside it carries its own word.
 */
export function Seam(): React.ReactNode {
  return (
    <span
      aria-hidden
      className="footer-seam mx-1.5 w-0 shrink-0 self-center border-l"
      data-testid="footer-seam"
    />
  );
}

/**
 * A run of groups with a seam between each PRESENT pair.
 *
 * Every group in this band is conditional — a root workspace has no worktree,
 * a clean tree has no dirty count — so `{cond ? <Seam/> : null}` beside each
 * one puts the divider's presence in the hands of whichever neighbour happens
 * to render, and the band opens or closes on a rule. Filtering the rendered
 * children once, here, makes a leading or trailing seam unrepresentable.
 *
 * A WRAPPER MUST NOT SHRINK PAST CONTENT THAT CANNOT, OR IT CLIPS ITS OWN
 * CHILD. Every wrapper used to be `min-w-0`, which lets flex shrink it below
 * its content's intrinsic width — right for a group holding an eliding `Value`,
 * wrong for one holding only figures, which have nothing to elide. The child
 * then overflows the wrapper, `Section`'s `overflow-hidden` cuts whatever
 * crosses the section edge, and it reads as a SPACING defect rather than a
 * sizing one: measured at 1024px, the context reading sat 4.9px past its
 * wrapper with its `%` sliced mid-glyph, leaving 24.3px of dead air before the
 * section rule against 6.4px after it. Nothing about that looks like a
 * min-width problem, which is why it survived the pass that added the clip.
 *
 * **The group itself already declares this and nothing was reading it.** A
 * group whose content cannot give way marks its own root `shrink-0` — the
 * runtime and context-window groups both did, before this bug existed — so the
 * wrapper mirrors its child rather than taking a second opinion from a prop a
 * caller could set inconsistently. An index list was the first design and is
 * unusable here: every group in this band is conditional, so positions shift
 * with the data and the wrong group goes rigid on a workspace with no branch.
 *
 * Dropping `min-w-0` from every wrapper was the other candidate, measured and
 * worse: it takes truncation away from the elastic groups too, so the agent
 * name stops eliding and the worst overflow goes 7.3px → 76.9px.
 */
function groupIsRigid(group: React.ReactNode): boolean {
  if (!React.isValidElement<{ className?: string }>(group)) return false;
  return /(^|\s)shrink-0(\s|$)/.test(group.props.className ?? "");
}

export function Groups({ children }: { children: React.ReactNode }): React.ReactNode {
  const groups = React.Children.toArray(children).filter(Boolean);
  return (
    <>
      {groups.map((group, index) => (
        // eslint-disable-next-line react/no-array-index-key -- position IS the identity of a seam
        <React.Fragment key={index}>
          {index > 0 ? <Seam /> : null}
          <span
            className={`inline-flex items-center gap-1 px-1 ${
              groupIsRigid(group) ? "shrink-0" : "min-w-0"
            }`}
          >
            {group}
          </span>
        </React.Fragment>
      ))}
    </>
  );
}

/** A lucide glyph at the band's text size, on the text's baseline. */
export function Glyph({ Icon, className = "" }: { Icon: LucideIcon; className?: string }): React.ReactNode {
  return <Icon aria-hidden className={`size-[1em] shrink-0 align-[-0.125em] ${className}`} />;
}

/**
 * THE TWO CEILINGS, IN `ch`, BECAUSE A FLEX FLOOR IS NOT A BOUND.
 *
 * Every value here used to be `min-w-0 truncate`, which sets no upper limit at
 * all: a name is drawn in full until the ROW runs out, and only then does the
 * browser take width from whichever sibling flex happened to pick. Measured on
 * the deployed band at 1600px, a 29-character plan held 133px of the strip
 * while `main` — four characters, unbounded in principle — was squeezed to 11px
 * of a needed 24 at 1024px. The long label wins and the short one is destroyed,
 * which is the reported defect and is backwards: nothing was ever too long,
 * because nothing had a length.
 *
 * So a ceiling is stated per KIND of value, in `ch`, which tracks the band's
 * own type rather than a pixel that the density root would silently re-scale.
 * The two tiers answer the one question worth asking — does the head of this
 * value identify it, or do you need most of it?
 *
 * `name` is a thing you RECOGNISE: a project, an agent. The first dozen
 * characters do the work (`bearlike.github.io` fits whole at 22ch), and a
 * reader who needs the rest is looking at the one value they came for.
 * `label` is a thing you CONFIRM: a plan, a branch, a path. You are checking
 * it against something you already know, so it is bounded tighter.
 *
 * Both still truncate under a `min-w-0` parent when the row is genuinely out of
 * room — the ceiling caps growth, it does not reserve space.
 */
const CEILING = {
  name: "max-w-[22ch]",
  label: "max-w-[14ch]",
} as const;

export type ValueCeiling = keyof typeof CEILING;

/**
 * A value that may elide, with its full form one hover or focus away.
 *
 * `title` defaults to the rendered text but can carry MORE than it — a strip
 * showing a plan can name the account behind it without printing it.
 *
 * Truncation is the primitive's, not the caller's: `min-w-0 truncate` was
 * repeated at every call site and a section that forgot it got a value that
 * pushed the band instead of eliding. A caller chooses WHICH ceiling applies,
 * never whether one does.
 */
export function Value({
  children,
  ceiling = "label",
  className = "",
  title,
}: {
  children: string;
  ceiling?: ValueCeiling;
  className?: string;
  title?: string;
}): React.ReactNode {
  const full = title ?? children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={`min-w-0 truncate ${CEILING[ceiling]} ${className}`}
          title={full}
          tabIndex={0}
        >
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent>{full}</TooltipContent>
    </Tooltip>
  );
}

/**
 * A figure with its one word: `3 working`, `2 dirty`, `22 tickets 82%`.
 *
 * The word is UNCONDITIONAL and tertiary. A count whose meaning rides in its
 * hue alone is §4.7's failure, and the last time a label was gated behind a
 * breakpoint a tablet rendered `1 0 1`. `tone` reaches the glyph and the
 * number; the word stays quiet so a row of figures reads as one run.
 */
export function Figure({
  Icon,
  count,
  word,
  tone = "text-content-secondary",
  suffix,
  title,
  testId,
}: {
  Icon: LucideIcon;
  count: number | string;
  word: string;
  tone?: string;
  /** A trailing qualifier such as a percentage, also tertiary. */
  suffix?: string | null;
  title?: string;
  testId?: string;
}): React.ReactNode {
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 ${tone}`} title={title} data-testid={testId}>
      <Glyph Icon={Icon} />
      <span className="tabular-nums">{count}</span>
      <span className="text-content-tertiary">{word}</span>
      {suffix ? <span className="tabular-nums text-content-tertiary">{suffix}</span> : null}
    </span>
  );
}
