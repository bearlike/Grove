import { FolderGit2Icon, GitBranchIcon, MapPinIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The entity vocabulary: one glyph per KIND of thing Grove names.
 *
 * WHY THIS EXISTS. A workspace subtitle used to read `Grove · main` — a project
 * and a branch in identical weight, colour and treatment, separated by a
 * middot. Nothing on that line was visually typed, so the reader had to infer
 * from POSITION that the first token was a project and the second a branch.
 * The middot was load-bearing punctuation standing in for the type information
 * the line never carried.
 *
 * A leading glyph makes the line parseable at a glance instead of read left to
 * right, and it makes the middot unnecessary: two typed entities need no
 * separator, because the glyph already says where one ends and the next begins.
 *
 * THE RULE: a project, a branch or a location reads the SAME WAY everywhere it
 * appears — the rail, a fleet card, a table cell, a header. That is the whole
 * point of putting it here rather than composing an icon beside a string at
 * each call site, and it is why these take no `icon` prop: a caller that can
 * choose the glyph is a caller that can disagree with every other caller.
 *
 * Icons are `aria-hidden` and the kind is restated in `sr-only` text. A screen
 * reader announcing "folder git 2 Grove" is worse than useless, but a reader
 * that hears only "Grove" has lost exactly the typing the glyph adds for
 * everyone else — so the type is spoken as a word rather than drawn.
 *
 * THE GLYPH IS SIZED IN `em`, NOT IN PIXELS. "Reads the same way everywhere"
 * has to mean the same PROPORTION, not the same absolute size: these labels sit
 * on a `text-sm` fleet card and on a `text-xs` subtitle in a 260px rail, and a
 * fixed 14px mark that looks right on the first is a heavy smudge on the
 * second — two of them on one 12px line is the "visual noise" this vocabulary
 * exists to avoid. At `1em` the mark tracks whatever type size the call site
 * already set, so no caller ever needs to override it, which is the property
 * that keeps the vocabulary from being negotiated per surface.
 */

/** Shared anatomy. Not exported: a caller picking its own glyph defeats the vocabulary. */
function EntityLabel({
  kind,
  value,
  Icon,
  mono,
  className = "",
}: {
  kind: string;
  value: string;
  Icon: React.ComponentType<React.SVGProps<SVGSVGElement>>;
  mono: boolean;
  className?: string;
}): React.ReactNode {
  return (
    // `min-w-0` on the row and `truncate` on the text: the glyph must never be
    // the thing that shrinks. A clipped branch name is legible; a clipped icon
    // is a smudge, and it is the part carrying the type.
    //
    // `cn`, not a template literal: tailwind-merge is what lets a caller's
    // `text-xs` actually replace an inherited size instead of both classes
    // landing and the winner being decided by stylesheet order.
    <span
      className={cn("inline-flex min-w-0 items-center gap-1", className)}
      title={`${kind}: ${value}`}
      data-testid={`entity-${kind.toLowerCase()}`}
    >
      <Icon aria-hidden className="size-[1em] shrink-0 text-muted-foreground" />
      <span className="sr-only">{kind}: </span>
      <span className={cn("truncate", mono && "font-mono")}>{value}</span>
    </span>
  );
}

/**
 * The floor a project name keeps before a branch sharing its row may crowd it
 * out.
 *
 * WHY THIS EXISTS. Two flex items with the default shrink algorithm split a
 * deficit proportional to each item's own basis, so the LARGER item is
 * shrunk by the LARGER absolute amount. A project name is routinely the
 * longer string of the pair (a repo name) beside a short branch (`main`) —
 * so without a floor, the project was the one that shrank away first, which
 * is backwards: it is the thing a reader identifies the row BY, and the
 * branch is secondary.
 *
 * WHY `calc(1em + 0.25rem + 8ch)` AND NOT JUST `8ch`. `ch` sizes the
 * guarantee to the readable NAME text, but `ProjectLabel` is icon-plus-text —
 * the icon is `size-[1em]` and sits a `gap-1` (`0.25rem`) from the string —
 * so an `8ch` floor on the whole element would let the icon and its gap eat
 * into the 8 characters it promises. Adding both back is what makes the
 * floor land on the text itself.
 *
 * WHY A MINIMUM AND NOT A FIXED WIDTH. A fixed cap (`max-w-*`) would clip the
 * project name even where the row has room to spare — the mobile sheet
 * renders this same row at close to the full viewport width, and there the
 * project name should show in FULL, with the branch beside it. A `min-width`
 * only ever binds when space is actually short: flexbox stops shrinking the
 * project at this floor and redirects the remaining deficit onto the
 * sibling that still has room, which is `BranchLabel` — it carries none of
 * its own. One rule, both widths.
 */
export const PROJECT_MIN_WIDTH = "min-w-[calc(1em_+_0.25rem_+_8ch)]";

/** A repository. `FolderGit2Icon` rather than a bare folder: a Grove project is always a git repo. */
export function ProjectLabel({
  name,
  className,
}: {
  name: string;
  className?: string;
}): React.ReactNode {
  return <EntityLabel kind="Project" value={name} Icon={FolderGit2Icon} mono={false} className={className} />;
}

/** A git branch. Mono, because a branch name is an identifier the user may have to retype. */
export function BranchLabel({
  name,
  className,
}: {
  name: string;
  className?: string;
}): React.ReactNode {
  return <EntityLabel kind="Branch" value={name} Icon={GitBranchIcon} mono className={className} />;
}

/**
 * A directory on disk — the coordinate a session ran in.
 *
 * Distinct from `ProjectLabel` on purpose: a session's `cwd` is frequently NOT
 * a repo root, and the catalog carries rows whose enclosing project is `None`.
 * Drawing a repo mark over a bare path would assert something the data does not.
 */
export function LocationLabel({
  path,
  className,
}: {
  path: string;
  className?: string;
}): React.ReactNode {
  return <EntityLabel kind="Location" value={path} Icon={MapPinIcon} mono className={className} />;
}
