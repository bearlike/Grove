"use client";

import {
  FileDiffIcon,
  FolderGit2Icon,
  HistoryIcon,
  type LucideIcon,
  MessagesSquareIcon,
  UserRoundIcon,
  WrenchIcon,
} from "lucide-react";

import { CardShell } from "@/components/grove/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { UsageSummaryView } from "@/lib/grove/api";
import { AbbreviatedNumber } from "./abbreviated-number";
import { SectionFailure } from "./section";

/** The tile grid, declared once: the loaded row, the skeleton row and the
 * failure all have to lay out on it, or the page reflows as the queries land. */
const TILES = "grid gap-4 grid-cols-2 lg:grid-cols-3 xl:grid-cols-6";

/**
 * The headline row: what this store actually measured, largest first.
 *
 * These six are the numbers the daemon reports on EVERY host, which is why they
 * lead. Tokens and cost are not among them — both are provider-dependent and
 * null here, so promoting them to the headline would make the page open on two
 * shrugs. They keep their own cards further down, where their absence can be
 * explained rather than merely displayed.
 */
export function UsageSummary({
  summary,
  failed,
  onRetry,
  retrying,
}: {
  summary: UsageSummaryView | undefined;
  failed: boolean;
  onRetry?: () => void;
  retrying?: boolean;
}): React.ReactNode {
  // A failure stays ON the grid, inside the same container a tile uses. As a
  // bare paragraph it was a 20px line where six 96px cards had been, so the
  // whole page below it jumped the moment this one query answered — and it read
  // as a note about the data rather than as a request that never landed.
  if (failed) {
    return (
      <div className={TILES}>
        <CardShell className="col-span-full p-4" data-testid="usage-totals-failed">
          <SectionFailure
            detail="Usage totals could not be loaded."
            onRetry={onRetry}
            retrying={retrying}
          />
        </CardShell>
      </div>
    );
  }

  if (!summary) {
    return (
      <div className={TILES} aria-hidden>
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} className="h-24 w-full" />
        ))}
      </div>
    );
  }

  return (
    <section className={TILES} aria-label="Usage totals" data-testid="usage-totals">
      <Stat label="Sessions" icon={HistoryIcon} value={summary.sessions} />
      <Stat label="Turns" icon={MessagesSquareIcon} value={summary.turns} />
      <Stat
        label="Tool calls"
        icon={WrenchIcon}
        value={summary.tools.calls}
        hint={
          <>
            <AbbreviatedNumber value={summary.tools.failures} /> failed ·{" "}
            <AbbreviatedNumber value={summary.tools.distinct_tools} /> distinct
          </>
        }
      />
      <Stat label="Files changed" icon={FileDiffIcon} value={summary.files_changed} />
      <Stat label="Projects" icon={FolderGit2Icon} value={summary.projects} />
      <Stat label="Accounts" icon={UserRoundIcon} value={summary.accounts} />
    </section>
  );
}

function Stat({
  label,
  icon: Icon,
  value,
  hint,
}: {
  label: string;
  /** Required, not optional: six tiles in one row, and the one without a glyph
   * reads as a bug rather than as a decision. */
  icon: LucideIcon;
  /** Not nullable, and the type is the guard. Every field this row shows is a
   * plain `number` on the wire, so an unmeasured tile is unreachable here —
   * which is why there is no step-down branch below. The absence rule still
   * applies wherever a figure genuinely can be null; it lives in
   * `AbbreviatedNumber` and in the cost card, which is the one headline figure
   * this host really cannot measure. */
  value: number;
  hint?: React.ReactNode;
}): React.ReactNode {
  return (
    // `CardShell` and not `SectionCard`: a stat tile's header is one word with
    // no action beside it, so a TINTED band and a rule would be more chrome
    // than content. It takes the shared container — radius, elevation, the
    // density reset — and composes its own header and body.
    //
    // THE HEADER IS TYPE AND SPACING ONLY. `SectionCard`'s tinted band and this
    // plain header are two different components doing two different jobs: a
    // band reads as a title bar for a THING, which is right when the header
    // carries an identity, a subtitle and a status, and wrong over a one-word
    // topic. Keeping the band's look off this tile is what lets both exist
    // without either drifting toward the other — do not "unify" them.
    <CardShell className="flex flex-col gap-1 p-4" data-testid="usage-stat" data-stat={label}>
      <header className="flex items-center gap-1 text-xs text-content-tertiary">
        {/* `size-[1em]`, never a fixed pixel size: the glyph is decoration on a
            LABEL, so it has to scale with the label's type rather than compete
            with it — the same rule `entity.tsx` states for its own marks. */}
        <Icon aria-hidden className="size-[1em] shrink-0" />
        <span>{label}</span>
      </header>
      <p className="text-2xl font-semibold text-content-primary">
        <AbbreviatedNumber value={value} />
      </p>
      {hint ? <p className="text-xs text-content-tertiary">{hint}</p> : null}
    </CardShell>
  );
}
