"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import type { LucideIcon } from "lucide-react";
import { FolderGit2Icon, GitBranchIcon, MapPinIcon, MonitorIcon } from "lucide-react";

import {
  ModelSelectorContent,
  ModelSelectorEmpty,
  ModelSelectorGroup,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorRoot,
  ModelSelectorSearch,
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
 * cannot end up wearing different marks. `agent`, `model` and `runtime` are
 * absent on purpose: each of those marks is per-VALUE data — an agent's brand
 * mark (`AgentMark`), a model's (`ModelMark`), the fleet's own runtime table —
 * and all three arrive through `leading`.
 */
const PILL_GLYPH: Readonly<
  Record<Exclude<LaunchPillKind, "agent" | "runtime" | "model">, LucideIcon>
> = {
  project: FolderGit2Icon,
  // `entity.tsx`'s Location mark, not a second folder: a working directory is a
  // PLACE INSIDE the project the pill beside it already named, and two folders
  // in a row would read as two repositories.
  directory: MapPinIcon,
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
  /**
   * This option is only half an answer — the rest is typed in the panel below,
   * so choosing it must NOT close the menu.
   *
   * The vendored `ModelSelectorItem` closes on select unconditionally, which is
   * right for an option that IS the answer and wrong for one that reveals a
   * field. Both the branch modes and the model pill's `Custom…` shipped with
   * their fields mounted inside a popover the same click had just dismissed:
   * the control looked like it did nothing, and the field was only reachable by
   * reopening the menu you had just been thrown out of.
   */
  readonly opensPanel?: boolean;
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

/**
 * Keys cmdk's `Command` root acts on, and therefore keys a nested field loses.
 *
 * The panel below the list holds real form controls — a branch name, a custom
 * model id — and they sit INSIDE the Command root, so every keystroke bubbles
 * into its list navigation. Enter is the expensive one: cmdk calls
 * `preventDefault()` and fires `cmdk-item-select` on whatever row is
 * highlighted, so pressing Enter after typing a branch name silently re-answers
 * the control with a different mode. Home/End jump the list instead of moving
 * the caret, and the vertical arrows move the highlight instead of nothing.
 *
 * ESCAPE IS DELIBERATELY ABSENT. Radix's dismiss layer listens on `document`,
 * and React routes `stopPropagation()` through to the native event — so
 * swallowing Escape here would leave the popover with no keyboard way out. Same
 * technique the vendored `ModelSelectorEffort` uses for Home/End; this is the
 * complete key set rather than the two that component happened to need.
 */
export function capturesCommandKey(event: Pick<KeyboardEvent, "key" | "ctrlKey">): boolean {
  switch (event.key) {
    case "Enter":
    case "Home":
    case "End":
    case "ArrowUp":
    case "ArrowDown":
      return true;
    // cmdk's vim bindings, which are on by default.
    case "n":
    case "j":
    case "p":
    case "k":
      return event.ctrlKey;
    default:
      return false;
  }
}

export interface LaunchPillProps {
  readonly kind: LaunchPillKind;
  /** Names the control for a screen reader; the visible text is the VALUE. */
  readonly ariaLabel: string;
  /** The currently selected option id, or null while nothing is resolved yet. */
  readonly value: string | null;
  readonly options: readonly LaunchPillOption[];
  readonly onSelect: (id: string) => void;
  /**
   * The plural noun this control searches — `"projects"`, `"models"`.
   *
   * Presence is what makes the list searchable, so the capability and the word
   * it needs cannot disagree: a boolean beside a string is two facts, and the
   * pair that drifted is exactly why this is one prop. A host with twenty repos
   * needs a search box; five branch modes do not.
   */
  readonly searchNoun?: string;
  /** The resolved answer before the relevant catalog has arrived. */
  readonly fallbackLabel?: string;
  /** Why the control is unavailable, e.g. "Choose a project first". */
  readonly disabledReason?: string;
  /**
   * What is wrong with the current answer, surfaced on the CLOSED trigger.
   *
   * A field that lives in the panel takes its error message with it when the
   * menu closes, and a Send button disabled for an invisible reason is the
   * defect that trade would buy. The panel still shows the sentence; this is
   * the half that survives dismissal.
   */
  readonly error?: string | null;
  /** A per-value mark where the glyph is data rather than a fixed control icon. */
  readonly leading?: ReactNode;
  /** Fields an `opensPanel` option reveals, below the list inside the popover. */
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
 *
 * THE POPOVER'S BODY IS COMPOSED HERE RATHER THAN DEFAULTED, and that is what
 * makes the control say what it is. `ModelSelectorContent`'s own defaults are
 * written for the one control it was built for: its keyboard anchor is
 * hard-coded `aria-label="Model"`, its search placeholder is
 * `"Search models..."` and its empty state is `"No models found."` — so a
 * screen reader met a combobox called Model inside the Agent picker, and
 * filtering the Project list to nothing said no models were found. Every one of
 * those is a vendored sub-part accepting the right words as props; passing
 * children is also what suppresses the mislabelled anchor, since the default
 * body is the only thing that renders it.
 */
export function LaunchPill({
  kind,
  ariaLabel,
  value,
  options,
  onSelect,
  searchNoun,
  fallbackLabel,
  disabledReason,
  error,
  leading,
  children,
}: LaunchPillProps): ReactNode {
  const { openKind, setOpenKind } = usePillGroup();
  const Glyph =
    kind === "agent" || kind === "runtime" || kind === "model"
      ? null
      : (PILL_GLYPH[kind] ?? FALLBACK_GLYPH);
  const selected = options.find((option) => option.id === value);
  const label = (selected?.opensPanel ? fallbackLabel : undefined) ?? selected?.label ?? fallbackLabel ?? "";
  const unavailable = disabledReason !== undefined;
  const mark = leading ?? (Glyph ? <Glyph aria-hidden /> : null);
  const modelOptions = useMemo(() => toModelOptions(options), [options]);
  const noun = searchNoun ?? ariaLabel.toLowerCase();
  // Set while the pill's own item handler runs, read by the close that Radix
  // fires immediately after it. A ref rather than state because the two happen
  // in one commit and a re-render between them would be the race.
  const keepOpen = useRef(false);

  // A control that becomes unavailable while its menu is open closes through
  // the `open` expression below, and Radix then returns focus to a trigger that
  // is now `disabled` — which cannot take it, so focus falls to the body. Drop
  // the row's key too, or the menu also springs back open by itself the moment
  // the control becomes available again.
  useEffect(() => {
    if (unavailable && openKind === kind) setOpenKind(null);
  }, [unavailable, openKind, kind, setOpenKind]);

  return (
    <ModelSelectorRoot
      models={modelOptions}
      value={value ?? ""}
      onValueChange={(id) => {
        keepOpen.current = options.find((option) => option.id === id)?.opensPanel === true;
        onSelect(id);
      }}
      open={openKind === kind && !unavailable}
      onOpenChange={(next) => {
        if (!next && keepOpen.current) {
          keepOpen.current = false;
          return;
        }
        setOpenKind(next ? kind : null);
      }}
    >
      <ModelSelectorTrigger
        variant="outline"
        size="sm"
        aria-label={ariaLabel}
        aria-invalid={error != null}
        // A pill truncates rather than wrapping, so the full value has to be
        // reachable on hover even when the visible text is clipped. The error
        // rides the same tooltip: it is the only account of itself a closed
        // control can give.
        title={
          unavailable
            ? disabledReason
            : error != null
              ? `${ariaLabel}: ${label} — ${error}`
              : `${ariaLabel}: ${label}`
        }
        disabled={unavailable}
        data-pill={kind}
        // gap-2 is the vendored spacing between the trigger's content and its
        // chevron; the inner gap-1.5 separates the mark from the word, so the
        // three read as mark · value · affordance rather than one smudge.
        //
        // `max-w-44` bounds the WORD, not the control: a project whose repo
        // name runs long, or a gateway model id, otherwise pushes its
        // neighbours off the line and the row re-wraps as the cascade answers.
        // The full text stays reachable through the title above.
        className="aria-invalid:text-destructive max-w-44 gap-2"
      >
        {/*
          EVERY pill spells its value out, including one still sitting on the
          cascade's answer. The row used to collapse an untouched agent, runtime
          and branch to a bare glyph on the argument that the mark said it
          already — but a mark cannot distinguish `Claude Code` from
          `Claude Code (via configured gateway)`, and three anonymous glyphs beside two
          worded pills read as decoration rather than as controls. The width
          that bought is now paid for by the row wrapping instead.
        */}
        <span className="flex min-w-0 items-center gap-1.5">
          {mark}
          <span className="truncate">{label}</span>
        </span>
      </ModelSelectorTrigger>
      {/*
        Wider than the vendored `w-72`, because these lists carry paths and
        provider ids rather than short product names, and an option truncated in
        its own picker is a value with nowhere left to be read.
      */}
      <ModelSelectorContent className="w-80">
        {searchNoun === undefined ? (
          // cmdk anchors list navigation on its input, so an unsearchable list
          // still needs one — hidden, read-only, and named after THIS control.
          <div className="sr-only">
            <ModelSelectorSearch readOnly aria-label={ariaLabel} />
          </div>
        ) : (
          <ModelSelectorSearch
            placeholder={`Search ${noun}…`}
            aria-label={`Search ${noun}`}
          />
        )}
        <ModelSelectorList>
          <ModelSelectorEmpty>{`No ${noun} found.`}</ModelSelectorEmpty>
          <ModelSelectorGroup>
            {modelOptions.map((option) => (
              // `title` is the item's own overflow escape hatch: the vendored
              // row truncates both its name and its description, and a working
              // directory or a gateway model id is exactly the value that runs
              // past the edge.
              <ModelSelectorItem
                key={option.id}
                model={option}
                title={option.description ? `${option.name} — ${option.description}` : option.name}
              />
            ))}
          </ModelSelectorGroup>
        </ModelSelectorList>
        {children ? (
          <div
            className="flex flex-col gap-2 border-t p-3"
            onKeyDown={(event) => {
              if (capturesCommandKey(event)) event.stopPropagation();
            }}
          >
            {children}
          </div>
        ) : null}
      </ModelSelectorContent>
    </ModelSelectorRoot>
  );
}
