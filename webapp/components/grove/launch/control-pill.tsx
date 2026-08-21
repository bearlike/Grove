"use client";

import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { CpuIcon, FolderGit2Icon, GitBranchIcon, MapPinIcon, MonitorIcon } from "lucide-react";

import {
  ModelSelectorContent,
  ModelSelectorRoot,
  ModelSelectorTrigger,
  type ModelOption,
} from "@/components/assistant-ui/model-selector";

/**
 * The controls the row can carry. A closed set, because the glyph table below
 * is keyed by it — a seventh pill is a design decision, not a new string.
 *
 * IT IS ALSO THE ROW'S OPEN-MENU KEY, so no two pills may share a member.
 * `project` and `directory` did for one release: `open={openKind === kind}`
 * matched both, so a click on either opened BOTH popovers stacked over each
 * other and the directory list — rendered second — was unreachable behind the
 * project list. Nothing failed; the control simply did not work.
 */
export type LaunchPillKind = "project" | "directory" | "agent" | "model" | "runtime" | "branch";

/**
 * One glyph per control, decided once.
 *
 * §7's rule is that a glyph belongs to the ENTITY, not to whoever renders it —
 * so this is a table rather than a prop, and two pills for the same concept
 * cannot end up wearing different marks. `agent` is absent on purpose: an
 * agent's mark is per-agent data (`AgentMark`), and `runtime` likewise resolves
 * per value from the fleet's own runtime table; both arrive through `leading`.
 */
const PILL_GLYPH: Readonly<Record<Exclude<LaunchPillKind, "agent" | "runtime">, LucideIcon>> = {
  project: FolderGit2Icon,
  // `entity.tsx`'s Location mark, not a second folder: a working directory is a
  // PLACE INSIDE the project the pill beside it already named, and two folders
  // in a row would read as two repositories.
  directory: MapPinIcon,
  model: CpuIcon,
  branch: GitBranchIcon,
};

const FALLBACK_GLYPH: LucideIcon = MonitorIcon;

/** One selectable answer. Maps onto the vendored `ModelOption` verbatim. */
export interface LaunchPillOption {
  readonly id: string;
  readonly label: string;
  /** One line under the label, when a choice needs its consequence stated. */
  readonly description?: string;
  readonly icon?: ReactNode;
  /** A branch already checked out elsewhere, an agent the repo has none of. */
  readonly disabled?: boolean;
  readonly keywords?: readonly string[];
}

interface PillGroup {
  readonly openKind: LaunchPillKind | null;
  readonly setOpenKind: (kind: LaunchPillKind | null) => void;
}

const PillGroupContext = createContext<PillGroup | null>(null);

/**
 * Makes the row's open menus mutually exclusive.
 *
 * Each pill used to own its open state, so clicking a second one left the first
 * hanging — several popovers stacked over a composer that is only 44rem wide,
 * with no way to tell which one your next click belonged to. One owner for
 * "which pill is open" is the only arrangement where opening B can close A,
 * because A cannot know B was clicked.
 */
export function LaunchPillGroup({ children }: { readonly children: ReactNode }): ReactNode {
  const [openKind, setOpenKind] = useState<LaunchPillKind | null>(null);
  const value = useMemo<PillGroup>(() => ({ openKind, setOpenKind }), [openKind]);
  return <PillGroupContext.Provider value={value}>{children}</PillGroupContext.Provider>;
}

function usePillGroup(): PillGroup {
  const ctx = useContext(PillGroupContext);
  if (!ctx) throw new Error("LaunchPill must be used inside <LaunchPillGroup>");
  return ctx;
}

export interface LaunchPillProps {
  readonly kind: LaunchPillKind;
  /** Names the control for a screen reader; the visible text is the VALUE. */
  readonly ariaLabel: string;
  /** The currently selected option id, or null while nothing is resolved yet. */
  readonly value: string | null;
  readonly options: readonly LaunchPillOption[];
  readonly onSelect: (id: string) => void;
  /** A host with twenty repos needs this; a two-option runtime does not. */
  readonly searchable?: boolean;
  /** The resolved answer before the relevant catalog has arrived. */
  readonly fallbackLabel?: string;
  /** Why the control is unavailable, e.g. "Choose a project first". */
  readonly disabledReason?: string;
  /** A per-value mark where the glyph is data rather than a fixed control icon. */
  readonly leading?: ReactNode;
  /** Branch mode supplies its own search/list plus extra fields. */
  readonly children?: ReactNode;
}

function toModelOptions(options: readonly LaunchPillOption[]): readonly ModelOption[] {
  return options.map((option) => ({
    id: option.id,
    name: option.label,
    ...(option.description === undefined ? {} : { description: option.description }),
    ...(option.icon === undefined ? {} : { icon: option.icon }),
    ...(option.disabled === undefined ? {} : { disabled: option.disabled }),
    ...(option.keywords === undefined ? {} : { keywords: [...option.keywords] }),
  }));
}

/**
 * One control in the composer's action row.
 *
 * Every pill uses the vendored `ModelSelector` anatomy rather than the
 * composer's own `ComposerModelTrigger`: that trigger takes its label as a
 * `string` and renders no icon, so a row built on it had no way to put a mark
 * beside a value. `ModelSelectorItem` also already lays an option out the way this row
 * needs it (icon, name on one line, description as a small subtitle), which the
 * composer's side-by-side item does not.
 */
export function LaunchPill({
  kind,
  ariaLabel,
  value,
  options,
  onSelect,
  searchable,
  fallbackLabel,
  disabledReason,
  leading,
  children,
}: LaunchPillProps): ReactNode {
  const { openKind, setOpenKind } = usePillGroup();
  const Glyph = kind === "agent" || kind === "runtime" ? null : (PILL_GLYPH[kind] ?? FALLBACK_GLYPH);
  const selected = options.find((option) => option.id === value);
  const label = selected?.label ?? fallbackLabel ?? "";
  const unavailable = disabledReason !== undefined;
  const mark = leading ?? (Glyph ? <Glyph aria-hidden /> : null);

  return (
    <ModelSelectorRoot
      models={toModelOptions(options)}
      value={value ?? ""}
      onValueChange={onSelect}
      open={openKind === kind && !unavailable}
      onOpenChange={(next) => setOpenKind(next ? kind : null)}
    >
      <ModelSelectorTrigger
        variant="ghost"
        size="sm"
        aria-label={ariaLabel}
        // A pill truncates rather than wrapping, so the full value has to be
        // reachable on hover even when the visible text is clipped.
        title={unavailable ? disabledReason : `${ariaLabel}: ${label}`}
        disabled={unavailable}
        data-pill={kind}
        // gap-2 is the vendored spacing between the trigger's content and its
        // chevron; the inner gap-1.5 separates the mark from the word, so the
        // three read as mark · value · affordance rather than one smudge.
        className="gap-2"
      >
        {/*
          EVERY pill spells its value out, including one still sitting on the
          cascade's answer. The row used to collapse an untouched agent, runtime
          and branch to a bare glyph on the argument that the mark said it
          already — but a mark cannot distinguish `Claude Code` from
          `Claude Code (via KK Gateway)`, and three anonymous glyphs beside two
          worded pills read as decoration rather than as controls. The width
          that bought is now paid for by the row wrapping instead.
        */}
        <span className="flex min-w-0 items-center gap-1.5">
          {mark}
          <span className="truncate">{label}</span>
        </span>
      </ModelSelectorTrigger>
      <ModelSelectorContent searchable={searchable ?? false}>{children}</ModelSelectorContent>
    </ModelSelectorRoot>
  );
}
