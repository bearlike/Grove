"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useState,
  type Dispatch,
  type ReactNode,
} from "react";

import { inheritedModel, openProblems, previewKey } from "@/lib/grove/adapters/launch";
import { problemFor } from "@/lib/grove/adapters/launch-flow";
import type { LaunchPreviewView, LaunchProblemView } from "@/lib/grove/api";
import { useLaunchPreview, useWorkspaceDefaults } from "@/lib/grove/hooks/launch";

/**
 * Which branch the workspace gets, and — for `root` — whether it gets one at
 * all. Mirrors the engine's `BranchMode` (`core/config.py`) name for name, so a
 * default arriving from `GET /defaults` needs no translation on the way in.
 */
export type BranchMode = "auto" | "new" | "existing" | "remote" | "root";

/**
 * Where the agent runs: host, container, or a configured remote runtime by its
 * `runtimes.<name>` block name. `null` means "whatever the cascade resolves".
 * The block name is the same string `grove create --runtime` takes, so one
 * value reads the same on every surface.
 */
export type RuntimeChoice = "host" | "container" | (string & {});

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
  /** An explicitly chosen roster profile, or the engine's currently resolved one. */
  readonly agentName: string | null;
  /**
   * A tool selected before its profile. The engine chooses the profile after it
   * resolves the runtime, so this is deliberately distinct from `agentName`.
   */
  readonly agentKind: NonNullable<import("@/lib/grove/api").CreateWorkspaceRequest["agent_kind"]> | null;
  /** The opaque provider model id, including an explicitly typed custom id. */
  readonly model: string | null;
  /** Whether the Model picker is on Custom rather than its resolved catalog entry. */
  readonly customModel: boolean;
  /**
   * The reasoning-effort level, or `null` for the agent tool's own default.
   * Reset with the agent, like `model`, because the levels belong to the kind.
   */
  readonly effort: string | null;
  readonly runtime: RuntimeChoice | null;
  /** On a federation hub, the node to create on; `null` lets the hub pick. */
  readonly node: string | null;
  readonly brief: boolean | null;
  /**
   * Whether this create runs the agent as a Grove-owned native session. `null`
   * means the roster entry's own default, which the agent pill displays; a
   * touched value rides the request as `native`. Reset with the agent, the
   * way `model` is, because the default belongs to the entry.
   */
  readonly native: boolean | null;
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
  /**
   * The project whose `GET /defaults` answer has been seeded, or `null`.
   *
   * Until it equals `values.repoRoot`, every untouched pill is showing a
   * placeholder rather than the cascade's answer.
   */
  readonly seededRepo: string | null;
  /**
   * Why that answer would be refused, from the same response, keyed by request
   * field. Held here rather than read beside the builder so ONE builder refuses
   * for every surface; `openProblems` drops the ones a touched field overrode.
   */
  readonly problems?: readonly LaunchProblemView[];
  /**
   * The engine's verdict on THIS draft (`POST /launch/preview`), with the
   * serialized launch request it describes. It outranks the seed's problems
   * only while that request is still the current one: a preview of an older
   * draft says nothing about the newer one, so it must neither refuse nor
   * clear it. `openProblems` (adapters) is the one place that decides.
   */
  readonly preview?: LaunchPreview;
  /** The explicit placement displaced by a remote runtime, if it can be restored. */
  readonly runtimePlacementUndo?: BranchMode | null;
  /** The profile shown before the engine selected one for a tool/runtime pair. */
  readonly profileUndo?: string | null;
  /**
   * A create the daemon refused (422) naming a field, against the launch
   * request it refused. Live while that request is unchanged, so it lands on
   * the owning control like a preview problem and clears once the person
   * changes anything the create would send.
   */
  readonly refusal?: { readonly request: string; readonly problem: LaunchProblemView };
}

export interface LaunchPreview {
  readonly request: string;
  readonly view: LaunchPreviewView;
}

export const LAUNCH_INITIAL_VALUES: LaunchValues = {
  repoRoot: null,
  projectCwd: null,
  selectedProjectCwd: null,
  agentName: null,
  agentKind: null,
  model: null,
  customModel: false,
  effort: null,
  runtime: null,
  node: null,
  brief: null,
  native: null,
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
  seededRepo: null,
  problems: [],
  runtimePlacementUndo: null,
  profileUndo: null,
};

export type LaunchAction =
  /** Fill in the cascade's answers. Never marks a field touched. */
  | {
      readonly type: "seed";
      readonly values: Partial<LaunchValues>;
      /** The project these answers were resolved for. */
      readonly repo?: string;
      /** The engine's refusals of those answers; replaces the previous set. */
      readonly problems?: readonly LaunchProblemView[];
    }
  /** The engine previewed a draft; replaces any earlier preview. */
  | { readonly type: "preview"; readonly preview: LaunchPreview }
  /** A create was refused for one field; recorded against what it sent. */
  | { readonly type: "refused"; readonly request: string; readonly problem: LaunchProblemView }
  /** The user changed a control. Marks it touched and applies the two rules below. */
  | {
      readonly type: "set";
      readonly values: Partial<LaunchValues>;
    }
  | { readonly type: "reset" };

function sameProblems(a: readonly LaunchProblemView[], b: readonly LaunchProblemView[]): boolean {
  return (
    a.length === b.length &&
    a.every((problem, at) => problem.field === b[at]!.field && problem.error === b[at]!.error)
  );
}

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
      const seededRepo = action.repo ?? state.seededRepo;
      // Compared by CONTENT: the response is re-parsed on every refetch, so an
      // identity check would report a change every time and loop the effect.
      const problems =
        action.problems !== undefined && !sameProblems(action.problems, state.problems ?? [])
          ? action.problems
          : state.problems;
      if (!changed && seededRepo === state.seededRepo && problems === state.problems) return state;
      return { ...state, values, touched: state.touched, seededRepo, problems };
    }

    case "preview":
      // The same answer for the same request is no change: a fresh state here
      // re-renders every consumer for nothing (the seed case's rule).
      if (
        state.preview?.request === action.preview.request &&
        state.preview.view === action.preview.view
      ) {
        return state;
      }
      return { ...state, preview: action.preview };

    case "refused":
      return { ...state, refusal: { request: action.request, problem: action.problem } };

    case "set": {
      const changed = Object.keys(action.values) as LaunchField[];
      let values: LaunchValues = { ...state.values, ...action.values };
      let touched = withTouched(state.touched, changed);
      let runtimePlacementUndo = state.runtimePlacementUndo;
      let profileUndo = state.profileUndo;

      // A tool is an intent, not its current profile. Keep the value the engine
      // resolves visible, but omit it from the request until a Profile pick.
      if (changed.includes("agentKind") && !changed.includes("agentName")) {
        profileUndo = state.values.agentName;
        values = { ...values, agentName: null };
        touched = withoutTouched(touched, "agentName");
      }
      if (changed.includes("agentName")) {
        values = { ...values, agentKind: null };
        touched = withoutTouched(touched, "agentKind");
        profileUndo = null;
      }

      // RULE 1 — the model catalog belongs to the agent.
      // `resolve_models` returns a per-agent list, so a model chosen for one
      // agent is very often not offered by the next. Clearing it AND forgetting
      // that it was touched is what lets the new agent's own default seed in;
      // clearing the value alone would leave an empty pill nobody filled.
      if ((changed.includes("agentName") || changed.includes("agentKind")) && !changed.includes("model")) {
        values = { ...values, model: null, customModel: false };
        touched = withoutTouched(withoutTouched(touched, "model"), "customModel");
      }
      // RULE 1c — so does the effort ladder. Claude Code's levels are not
      // Codex's, and an agent with none would refuse any value at create.
      if ((changed.includes("agentName") || changed.includes("agentKind")) && !changed.includes("effort")) {
        values = { ...values, effort: null };
        touched = withoutTouched(touched, "effort");
      }
      // RULE 1b — the launch mode belongs to the agent too. Its default is
      // the roster entry's `native`, so a choice made against one entry is
      // not an answer about the next; forgetting it lets the new entry's own
      // default show, and an untouched pill says what will actually happen.
      if ((changed.includes("agentName") || changed.includes("agentKind")) && !changed.includes("native")) {
        values = { ...values, native: null };
        touched = withoutTouched(touched, "native");
      }

      // RULE 2 — a remote runtime needs an isolated branch. The draft moves the
      // current checkout to a new branch and remembers it did, so leaving the
      // remote runtime puts the person's own checkout back: the switch was the
      // runtime's requirement, not their choice, and must not outlive it.
      if (changed.includes("runtime")) {
        const remote = action.values.runtime !== null && action.values.runtime !== "host" && action.values.runtime !== "container";
        if (remote && state.values.branchMode === "root") {
          runtimePlacementUndo = "root";
          values = { ...values, branchMode: "auto" };
          touched = withTouched(touched, ["branchMode"]);
        } else if (!remote && state.runtimePlacementUndo != null) {
          values = { ...values, branchMode: state.runtimePlacementUndo };
          runtimePlacementUndo = null;
        }
        if (!touched.has("agentName")) {
          profileUndo = state.values.agentName;
          values = { ...values, agentName: null };
        }
      }
      if (changed.includes("branchMode")) runtimePlacementUndo = null;

      // RULE 3 — root placement carries `skip_init`, visibly.
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

      return { ...state, values, touched, runtimePlacementUndo, profileUndo };
    }
  }
}

export interface LaunchControls extends LaunchState {
  readonly seed: (
    values: Partial<LaunchValues>,
    repo?: string,
    problems?: readonly LaunchProblemView[],
  ) => void;
  readonly set: (values: Partial<LaunchValues>) => void;
  readonly reset: () => void;
  /** Record a create refusal that names its field, for the owning control. */
  readonly refused: (request: string, problem: LaunchProblemView) => void;
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
  const seed = useCallback(
    (values: Partial<LaunchValues>, repo?: string, problems?: readonly LaunchProblemView[]) =>
      dispatch({ type: "seed", values, repo, problems }),
    [],
  );
  const set = useCallback((values: Partial<LaunchValues>) => dispatch({ type: "set", values }), []);
  const reset = useCallback(() => dispatch({ type: "reset" }), []);
  const refused = useCallback(
    (request: string, problem: LaunchProblemView) => dispatch({ type: "refused", request, problem }),
    [],
  );
  const value = useMemo<LaunchControls>(
    () => ({ ...state, seed, set, reset, refused }),
    [state, seed, set, reset, refused],
  );
  return (
    <LaunchStateContext.Provider value={value}>
      <LaunchDefaultsSeed />
      <LaunchPreviewer dispatch={dispatch} />
      {children}
    </LaunchStateContext.Provider>
  );
}

/**
 * Seeds the cascade's answers for the selected project and agent.
 *
 * It lives with the state rather than in one surface's control row because
 * BOTH creation surfaces need it: the landing composer and the More options
 * dialog opened from the fleet, which mounts no landing page. Its dependency
 * list is the fix for the render loop `launchReducer` documents.
 */
function LaunchDefaultsSeed(): null {
  const { values, seed, touched, profileUndo } = useLaunchControls();
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const agentName = values.agentName;
  const repo = values.repoRoot;
  const engineProfilePending = touched.has("agentKind") || profileUndo != null;

  useEffect(() => {
    const resolved = defaults.data;
    if (!resolved || repo === null) return;
    // Seeded for the agent actually selected and re-seeded when it changes (an
    // agent switch clears an untouched model), so another agent never shows
    // the saved agent's model.
    seed(
      {
        agentName: engineProfilePending ? undefined : resolved.agent,
        runtime: resolved.runtime,
        brief: resolved.brief,
        model: engineProfilePending ? undefined : inheritedModel(resolved, agentName ?? resolved.agent ?? null),
        branchMode: resolved.branch_mode,
        baseRef: resolved.base_ref,
        skipInit: resolved.skip_init,
      },
      repo,
      resolved.problems,
    );
  }, [defaults.data, seed, agentName, repo, engineProfilePending]);
  return null;
}

/** How long the draft must sit still before the engine is asked about it. */
const PREVIEW_DEBOUNCE_MS = 250;

/**
 * Asks the engine what the current draft would launch, and records the answer
 * against the exact request it describes.
 *
 * Debounced so a typed branch name is one question, not one per keystroke;
 * the daemon side does no git, tmux or Docker, so the debounce is about
 * chatter, not cost. The answer is a VALIDATION — an empty problem list means
 * the engine would accept the request, never that provisioning will succeed.
 */
function LaunchPreviewer({ dispatch }: { readonly dispatch: Dispatch<LaunchAction> }): null {
  const state = useLaunchControls();
  const key = previewKey(state);
  const [settled, setSettled] = useState<string | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(key), PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [key]);
  const preview = useLaunchPreview(settled);
  useEffect(() => {
    if (preview.data && settled !== null) {
      dispatch({ type: "preview", preview: { request: settled, view: preview.data } });
    }
  }, [dispatch, preview.data, settled]);
  return null;
}

export function useLaunchControls(): LaunchControls {
  const ctx = useContext(LaunchStateContext);
  if (!ctx) throw new Error("useLaunchControls must be used inside <LaunchStateProvider>");
  return ctx;
}

/**
 * The engine's refusal owned by one control, while that control is untouched.
 * Every pill reads its problem here so none can decide differently.
 */
export function useLaunchProblem(field: LaunchField): string | null {
  return problemFor(openProblems(useLaunchControls()), field);
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
  attach: "launch-attach",
  attachments: "launch-attachments",
  overflow: "launch-overflow",
  expand: "launch-expand",
  expanded: "launch-expanded",
  suggestions: "launch-suggestions",
  footerLinks: "launch-footer-links",
  error: "launch-error",
  refusal: "launch-refusal",
  customModel: "launch-custom-model",
  customModelError: "launch-custom-model-error",
  sessionMode: "launch-session-mode",
} as const;
