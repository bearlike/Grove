"use client";

import { InfoIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";

/**
 * A short control label with its caveat one disclosure away.
 *
 * FOUR IDENTICAL COPIES existed — `SessionRegion`, `ControlLabel`, `ShareLabel`
 * and `PolicyLabel` each spelled the same label-plus-trigger row, each with the
 * same `About <label>` name, and each rendered a literal `(i)` string where the
 * icon belongs. Four real callers is what licenses one atom here rather than a
 * fifth copy; the composition is still `TooltipIconButton`, unchanged, so this
 * adds a shared shape and no new mechanism.
 *
 * THE GLYPH IS `size-3.5` AND NOT THE BUTTON'S DEFAULT `size-4`. The trigger
 * sits beside a 12px label, and the vendored icon button's own `[&_svg]:size-4`
 * put a 16px mark next to it — the mark outweighing the word it annotates is
 * exactly what read as "a second prominent action beside every label".
 *
 * THE BUTTON KEEPS ITS 24px BOX while the glyph shrinks inside it. Text size
 * and hit area are separate decisions (design-system §1): a quieter mark must
 * not cost a pointer or a finger its target.
 *
 * QUIET AT REST, BOUNDED ON FOCUS — `border-transparent` plus the ring, the
 * same pair the pane tabs use. The old triggers carried a painted border at
 * all times, which is what made a help mark read as a second square action
 * beside every label; dropping the border outright was the obvious fix and it
 * silently took the focus indicator with it (caught by the e2e focus census,
 * not by reading). A transparent border still reserves the width, so
 * `focus-visible:border-ring` has something to colour and nothing shifts when
 * it does.
 *
 * THAT 24px IS AN EXPLICIT MINIMUM, NOT `size-6`. Measured on the built app:
 * `size-6` is 1.5rem, which at the 80% fine-pointer root renders **19.2px**,
 * not 24 — the trap `webapp/CLAUDE.md` states in as many words. Dropping the
 * old triggers' `min-h`/`min-w` shrank every help target by a fifth, which is
 * the opposite of what "the mark gets quieter, the target does not" claims.
 * The `min-*` pair is in px on purpose: it is a floor under a POINTER, which
 * has a fixed physical size, so it must not scale with a density lever aimed
 * at type. The coarse-pointer root override lifts it further for a finger.
 *
 * `TooltipIconButton` renders the tooltip text a second time in an `sr-only`
 * span, so the accessible name and the caveat are both announced without a
 * `title`; Radix opens it on keyboard focus and closes it on Escape. The button
 * is a real focusable control and is never nested inside another one.
 */
export function HelpHint({ label, tooltip }: { label: string; tooltip: string }) {
  return (
    <TooltipIconButton
      variant="ghost"
      tooltip={tooltip}
      aria-label={`About ${label}`}
      className="size-6 min-h-[24px] min-w-[24px] shrink-0 border border-transparent text-content-tertiary focus-visible:border-ring focus-visible:ring-ring/50"
    >
      <InfoIcon aria-hidden className="size-3.5" />
    </TooltipIconButton>
  );
}

/**
 * The label and its trigger are one row, so the pair never wraps apart.
 *
 * EVERY ELEMENT HERE IS PHRASING CONTENT, deliberately. Two of the four callers
 * render this inside a `CardField`'s `<dt>`, and the `<p>` the old copies used
 * cannot legally nest inside the `<span>` that keeps the pair on one line. A
 * label is one short noun phrase in all four places, so nothing is lost.
 *
 * `inherit` is not a styling knob, it is WHOSE TYPE THE LABEL IS. A standalone
 * label above a region takes §1's `font-medium` card-title weight; one inside a
 * `<dt>` inherits the field name's size and tertiary tier already declared
 * there, and re-stating them would give one field label a rank its siblings
 * do not have.
 *
 * THE LABEL WRAPS; IT DOES NOT TRUNCATE. It shipped as `truncate` and rendered
 * `Link exp…` in the policy card — caught by looking at the built surface, not
 * by any assertion. A `CardFields` label column is `auto`, so it takes the
 * width its content needs; truncating there does not save space, it only
 * destroys the name while the row beside it stays half empty. These labels are
 * two words at most, so a wrap costs one line in the narrowest pane and keeps
 * the word. The trigger is `shrink-0`, which is what stops the icon being the
 * thing that gives way instead — a mark squeezed to 19px is unreadable AND
 * unclickable, where a wrapped label is merely two lines.
 */
export function HelpLabel({
  label,
  tooltip,
  inherit = false,
}: {
  label: string;
  tooltip: string;
  /** The enclosing element already types this label — a `CardField` `<dt>`. */
  inherit?: boolean;
}) {
  return (
    <span className="flex min-w-0 items-center gap-1">
      <span className={inherit ? "min-w-0" : "min-w-0 text-xs font-medium"}>{label}</span>
      <HelpHint label={label} tooltip={tooltip} />
    </span>
  );
}
