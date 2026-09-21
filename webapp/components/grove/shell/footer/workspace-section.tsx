"use client";

import {
  ChevronRightIcon,
  FolderGit2Icon,
  FolderIcon,
  FolderTreeIcon,
  GaugeCircleIcon,
  GitBranchIcon,
} from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { runtimeGlyph, runtimeLabel } from "@/components/grove/fleet/tokens";
import { Glyph, Groups, Section, Value } from "@/components/grove/shell/footer/primitives";
import { contextTone, formatContextWindow, type ContextTone } from "@/lib/grove/adapters/context";
import type { FooterContext } from "@/lib/grove/adapters/footer";

/** The ramp as TEXT tone — the same thresholds the Activity meter's bar uses. */
const PERCENT_TONE: Record<ContextTone, string> = {
  info: "text-info",
  success: "text-success",
  warning: "text-warning",
  destructive: "text-destructive",
};

/**
 * WHERE YOU ARE: project · runtime · branch · worktree · agent · context.
 *
 * Runtime is the section's second group rather than a stray in the fleet
 * aggregate, where it used to sit between a session count and a ticket count:
 * it is a fixed property of THIS workspace, and a reader asking "host or
 * container" looks beside the project name. That is VS Code's status-bar rule
 * — items about the whole workspace on the left, together.
 *
 * ONE FACE, AND THE RAIL IS THE PRECEDENT. The branch and the worktree used to
 * be `font-mono` on the argument that a ref is read character by character —
 * true where you stop to read one, which is why they are still mono on a fleet
 * card, in the Info tab and in every table. This band is the other case: it is
 * chrome, scanned at a glance, and the rail already carries the same exemption
 * for the same reason (design system §3, "no Grove code inside `AppSidebar`
 * sets `font-mono`"). A second face in a 24px strip costs ~15% more width for
 * text nobody is transcribing, and it was the only mono in the whole band —
 * one word in a different typeface reads as a defect rather than a category.
 * `tabular-nums` survives independently: digit alignment was never the face's
 * job.
 */
export function WorkspaceSection({ context }: { context: FooterContext }): React.ReactNode {
  const hasContext = Boolean(context.project || context.subpath || context.branch || context.worktree);
  const window = formatContextWindow(context.contextWindow?.used, context.contextWindow?.size);
  const RuntimeIcon = context.runtime ? runtimeGlyph(context.runtime) : null;
  if (!hasContext && !window && !RuntimeIcon) return null;

  return (
    <Section id="footer-workspace" accent className="shrink min-w-0">
      <Groups>
        {context.project ? (
          <span className="inline-flex min-w-0 items-center gap-1" data-testid="footer-project">
            <Glyph Icon={FolderGit2Icon} />
            <Value ceiling="name">{context.project}</Value>
            {/* The sub-path stays INSIDE the project group: `Grove › webapp` is
                one location in two parts and the chevron is already its
                separator. NO INNER BREAKPOINT here — this strip only renders
                at `lg` and up, so a `sm:`/`md:` gate can never be false. */}
            {context.subpath ? (
              <>
                <Glyph Icon={ChevronRightIcon} className="text-content-tertiary" />
                <Glyph Icon={FolderIcon} className="text-content-tertiary" />
                <Value className="text-content-tertiary">{context.subpath}</Value>
              </>
            ) : null}
          </span>
        ) : null}
        {RuntimeIcon ? (
          <span
            className="inline-flex shrink-0 items-center gap-1"
            title={`This workspace runs on the ${runtimeLabel(context.runtime!).toLowerCase()}`}
            data-testid="footer-runtime"
          >
            <Glyph Icon={RuntimeIcon} />
            <span>{runtimeLabel(context.runtime!)}</span>
          </span>
        ) : null}
        {context.branch ? (
          <span className="inline-flex min-w-0 items-center gap-1" data-testid="footer-branch">
            <Glyph Icon={GitBranchIcon} />
            <Value>{context.branch}</Value>
          </span>
        ) : null}
        {context.worktree ? (
          <span className="inline-flex min-w-0 items-center gap-1 text-content-tertiary" data-testid="footer-worktree">
            <Glyph Icon={FolderTreeIcon} />
            <Value>{context.worktree}</Value>
          </span>
        ) : null}
        {/* The agent's BRAND MARK, not a generic bot glyph: it is the identity
            every fleet row already leads with, so a reader who knows the rail
            recognises the band. The name stays as text — a logo alone is a
            colour-only carrier for anyone who does not know the logo. */}
        {context.rootAgent ? (
          <span className="inline-flex min-w-0 items-center gap-1" data-testid="footer-root-agent">
            <AgentMark agentName={context.rootAgent} className="size-[1em] shrink-0" />
            {/* `name`, not `label`: this is the one value in the group a
                reader recognises rather than checks, and `max-w-40` was a
                PIXEL bound — 160px at a 16px root, but the band renders at the
                80% density root, so it silently meant something else here. */}
            <Value ceiling="name" title={context.rootAgent}>{context.rootAgent}</Value>
          </span>
        ) : null}
        {window ? (
          <span
            className="inline-flex shrink-0 items-center gap-1"
            title={context.rootAgent ? `${context.rootAgent}: ${window.exactCounts}` : window.exactCounts}
            data-testid="footer-context-window"
          >
            <Glyph Icon={GaugeCircleIcon} />
            {/* THE COUNTS ARE THE PART THAT GIVES WAY, AND THE PERCENTAGE
                NEVER DOES. This group cannot shrink — both halves are figures
                — so below `xl` it was what pushed the section past its own
                closing rule and into the git section's first value (measured
                at 1024px: the section ended at 429px with this drawn to 449px).
                Clipping it was the first repair and it was worse: the box cut
                the RIGHT-hand child, which is the percentage, so the one value
                carrying urgency was the one deleted while the raw counts
                survived whole. A narrow band shows FEWER values rather than
                partial ones — the exact rule #815 established for this band —
                and `126.2K / 875.9K` is the half a reader can reconstruct,
                while `14%` is the half they act on. The counts stay one hover
                away in the title, exactly and unrounded. */}
            <span className="hidden shrink-0 tabular-nums xl:inline">{window.counts}</span>
            {/* The same four-band ramp the Activity meter paints on its bar,
                so one reading does not look calm in the band and amber in the
                card. The COUNTS stay quiet — the percentage is the value that
                carries urgency, and toning both would make the group shout. */}
            <span className={`shrink-0 tabular-nums ${PERCENT_TONE[contextTone(window.percentValue)]}`}>
              {window.percentWhole}%
            </span>
          </span>
        ) : context.contextUnavailable === "stale_native_worker" ? (
          // SUPPRESSED, not unmeasured — and the difference has a remedy. A
          // worker older than the current-format producer still emits the
          // cumulative counters that read 29.4M against a 1M window, so the
          // reading is withheld; saying nothing made that indistinguishable
          // from an agent that simply has not answered yet.
          <span
            className="inline-flex shrink-0 items-center gap-1 text-warning"
            title="This workspace's agent started before the context fix. Respawn it to measure the context window again."
            data-testid="footer-context-stale"
          >
            <Glyph Icon={GaugeCircleIcon} />
            <span>context needs respawn</span>
          </span>
        ) : null}
      </Groups>
    </Section>
  );
}
