"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";

/**
 * Which branch the workspace gets, and — for `root` — whether it gets one at
 * all. Mirrors the engine's `BranchMode` (`core/config.py`) name for name, so a
 * default arriving from `GET /defaults` needs no translation on the way in.
 */
export type BranchMode = "auto" | "new" | "existing" | "remote" | "root";

/** Where the agent runs. `null` means "whatever the cascade resolves". */
export type RuntimeChoice = "host" | "container";

/** The minimum a ticket suggestion needs to build a `TicketSelector`. */
export interface LaunchTicket {
  readonly provider: string;
  readonly id: string;
  readonly title?: string;
}

/**
 * Every answer the composer collects, in the shape the controls edit it.
 *
 * This is deliberately NOT `CreateWorkspaceRequest`: the request omits whatever
 * the user never touched, and these are the *displayed* values, which include
 * the cascade's answers. `buildCreateRequest` is what turns one into the other,
 * and it needs `touched` to do it — see `LaunchState`.
 */
export interface LaunchValues {
  readonly repoRoot: string | null;
  /** The one `project_cwd` wire value: where the agent starts. */
  readonly projectCwd: string | null;
  /** The selected registered project's cwd, used to preserve nested-project identity. */
  readonly selectedProjectCwd: string | null;
  readonly agentName: string | null;
  /** The opaque provider model id, including an explicitly typed custom id. */
  readonly model: string | null;
  /** Whether the Model picker is on Custom rather than its resolved catalog entry. */
  readonly customModel: boolean;
  readonly runtime: RuntimeChoice | null;
  readonly brief: boolean | null;
  readonly branchMode: BranchMode;
  /** `new` — the branch to create. */
  readonly branchName: string;
  /** `existing` — a local branch that is not checked out anywhere else. */
  readonly existingBranch: string;
  /** `remote` — the `origin/foo` ref to track. */
  readonly remoteRef: string;
  /** `remote` — optional local name; empty means "derive it from the ref". */
  readonly localName: string;
  readonly baseRef: string | null;
  readonly skipInit: boolean;
  /** Set only when the user renames it; otherwise the title is derived. */
  readonly titleOverride: string | null;
  readonly ticket: LaunchTicket | null;
}

export type LaunchField = keyof LaunchValues;

export interface LaunchState {
  readonly values: LaunchValues;
  /**
   * The fields the USER changed, as opposed to the ones the cascade answered.
   *
   * This is the load-bearing distinction on this whole surface. A pill displays
   * the resolved default so the user can see what will happen, but sending that
   * displayed value back on create would pin today's config into the workspace
   * forever — the request's `null` means "resolve it at create time", and a
   * resolved value means "this exact one, always". So the request carries only
   * what is in here.
   */
  readonly touched: ReadonlySet<LaunchField>;
}

export const LAUNCH_INITIAL_VALUES: LaunchValues = {
  repoRoot: null,
  projectCwd: null,
  selectedProjectCwd: null,
  agentName: null,
  model: null,
  customModel: false,
  runtime: null,
  brief: null,
  branchMode: "auto",
  branchName: "",
  existingBranch: "",
  remoteRef: "",
  localName: "",
  baseRef: null,
  skipInit: false,
  titleOverride: null,
  ticket: null,
};

export const LAUNCH_INITIAL_STATE: LaunchState = {
  values: LAUNCH_INITIAL_VALUES,
  touched: new Set<LaunchField>(),
};

export type LaunchAction =
  /** Fill in the cascade's answers. Never marks a field touched. */
  | { readonly type: "seed"; readonly values: Partial<LaunchValues> }
  /** The user changed a control. Marks it touched and applies the two rules below. */
  | {
      readonly type: "set";
      readonly values: Partial<LaunchValues>;
    }
  | { readonly type: "reset" };

function withTouched(
  touched: ReadonlySet<LaunchField>,
  fields: readonly LaunchField[],
): ReadonlySet<LaunchField> {
  const next = new Set(touched);
  for (const field of fields) next.add(field);
  return next;
}

function withoutTouched(
  touched: ReadonlySet<LaunchField>,
  field: LaunchField,
): ReadonlySet<LaunchField> {
  const next = new Set(touched);
  next.delete(field);
  return next;
}

/**
 * The control row's whole decision table, as a pure function.
 *
 * Exported so it is unit-testable with no DOM: the two cross-field rules below
 * are the kind of thing that is wrong for a week if it only ever runs inside a
 * component.
 */
export function launchReducer(state: LaunchState, action: LaunchAction): LaunchState {
  switch (action.type) {
    case "reset":
      return LAUNCH_INITIAL_STATE;

    case "seed": {
      // A seed never overwrites a field the user already answered. Defaults can
      // arrive after first paint (`GET /defaults` is a round trip), and a late
      // response clobbering a choice made in the meantime is a control that
      // silently reverts under the user's hands.
      const values = { ...state.values };
      let changed = false;
      for (const [key, value] of Object.entries(action.values)) {
        const field = key as LaunchField;
        if (state.touched.has(field)) continue;
        if (value === undefined) continue;
        if (Object.is(values[field], value)) continue;
        Object.assign(values, { [field]: value });
        changed = true;
      }
      // RETURNING THE SAME STATE WHEN NOTHING MOVED IS LOAD-BEARING, not a
      // micro-optimisation. The seed runs from an effect keyed on the defaults
      // response; a fresh object every time meant a new context value, a new
      // `seed` identity, the effect firing again — an unbounded render loop.
      //
      // It was invisible in the obvious places: the loop reconciles to
      // identical DOM, so a mutation observer reads zero and the frame rate
      // stays at 60. What it DID break was React transitions, which are lower
      // priority than the loop and so never got to commit — every client-side
      // navigation away from this surface silently did nothing, with the
      // target route's payload already fetched and waiting.
      if (!changed) return state;
      return { values, touched: state.touched };
    }

    case "set": {
      const changed = Object.keys(action.values) as LaunchField[];
      let values: LaunchValues = { ...state.values, ...action.values };
      let touched = withTouched(state.touched, changed);

      // RULE 1 — the model catalog belongs to the agent.
      // `resolve_models` returns a per-agent list, so a model chosen for one
      // agent is very often not offered by the next. Clearing it AND forgetting
      // that it was touched is what lets the new agent's own default seed in;
      // clearing the value alone would leave an empty pill nobody filled.
      if (changed.includes("agentName") && !changed.includes("model")) {
        values = { ...values, model: null, customModel: false };
        touched = withoutTouched(withoutTouched(touched, "model"), "customModel");
      }

      // RULE 2 — root placement carries `skip_init`, visibly.
      // The TUI does this too (`tui/screens/create.py:676-680`): an init script
      // written for a fresh worktree can be unsafe in the live repo root. Set it
      // and mark it touched so the request actually carries it — but the
      // checkbox stays enabled, because deciding this silently is exactly the
      // undisclosed choice this surface is supposed to stop making.
      if (changed.includes("branchMode") && !changed.includes("skipInit")) {
        const toRoot = action.values.branchMode === "root";
        const fromRoot = state.values.branchMode === "root";
        if (toRoot !== fromRoot) {
          values = { ...values, skipInit: toRoot };
          touched = toRoot
            ? withTouched(touched, ["skipInit"])
            : withoutTouched(touched, "skipInit");
        }
      }

      return { values, touched };
    }
  }
}

export interface LaunchControls extends LaunchState {
  readonly seed: (values: Partial<LaunchValues>) => void;
  readonly set: (values: Partial<LaunchValues>) => void;
  readonly reset: () => void;
}

const LaunchStateContext = createContext<LaunchControls | null>(null);

/**
 * Holds the control row's answers for one Launch surface.
 *
 * Component state rather than a client store, per the app's state split: this
 * is ephemeral UI state scoped to one route, nothing else reads it, and it must
 * NOT survive a navigation — a prompt and a branch choice left over from a
 * create you already made is a workspace you did not mean to start.
 */
export function LaunchStateProvider({ children }: { readonly children: ReactNode }) {
  const [state, dispatch] = useReducer(launchReducer, LAUNCH_INITIAL_STATE);
  // The three actions are pinned to `dispatch`, which useReducer guarantees is
  // stable — so a component may depend on `seed` in an effect without that
  // effect re-running every time the state it seeds changes. Building them
  // inline in the memo below made their identity track the state, which is the
  // other half of the loop the reducer's `seed` case describes.
  const seed = useCallback((values: Partial<LaunchValues>) => dispatch({ type: "seed", values }), []);
  const set = useCallback((values: Partial<LaunchValues>) => dispatch({ type: "set", values }), []);
  const reset = useCallback(() => dispatch({ type: "reset" }), []);
  const value = useMemo<LaunchControls>(
    () => ({ values: state.values, touched: state.touched, seed, set, reset }),
    [state, seed, set, reset],
  );
  return <LaunchStateContext.Provider value={value}>{children}</LaunchStateContext.Provider>;
}

export function useLaunchControls(): LaunchControls {
  const ctx = useContext(LaunchStateContext);
  if (!ctx) throw new Error("useLaunchControls must be used inside <LaunchStateProvider>");
  return ctx;
}

export type LaunchDispatch = Dispatch<LaunchAction>;

/**
 * The region names a static render can assert, and the ORDER is the contract.
 *
 * One table so the surface and the controls cannot disagree about what a region
 * is called — they are built by different hands and land in each other's slots.
 */
export const LAUNCH_TESTIDS = {
  page: "launch-page",
  brand: "launch-brand",
  headline: "launch-headline",
  composer: "launch-composer",
  input: "launch-input",
  controls: "launch-controls",
  overflow: "launch-overflow",
  expand: "launch-expand",
  expanded: "launch-expanded",
  derivedTitle: "launch-derived-title",
  suggestions: "launch-suggestions",
  footerLinks: "launch-footer-links",
  error: "launch-error",
  customModel: "launch-custom-model",
  customModelError: "launch-custom-model-error",
} as const;
