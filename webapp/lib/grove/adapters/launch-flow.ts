import type {
  AgentSummaryView,
  LaunchProblemView,
  RuntimeOptionView as RuntimeOption,
  WorkspaceDefaultsView,
} from "@/lib/grove/api";
import type {
  BranchMode,
  LaunchField,
  LaunchState,
  LaunchValues,
} from "@/components/grove/launch/launch-state";
import { PROVIDER_LABEL } from "./footer";
import { DRAFT_FIELD } from "./launch";

/**
 * The approved creation flow — Project/location → Runtime → Agent, with a
 * conditional Profile — as pure decisions over data the client already has.
 *
 * Nothing here resolves a default or knows a provider's capabilities: tools are
 * GROUPED from the roster the engine returned, and every effective value comes
 * from the draft (which the engine's own `GET /defaults` seeded). The engine's
 * per-field provenance arrives through `LaunchSources`; until the generated
 * schema carries it, callers pass none and the summary states no source.
 */

type AgentKind = AgentSummaryView["kind"];

/** One tool in the Agent picker: every roster entry sharing a kind. */
export interface AgentTool {
  readonly kind: AgentKind;
  readonly label: string;
  /** The roster entries ("profiles") of this tool, in roster order. */
  readonly profiles: readonly AgentSummaryView[];
}

/** A kind's display word: the shared table, or the wire word verbatim. */
export function toolLabel(kind: AgentKind): string {
  return PROVIDER_LABEL[kind] ?? kind;
}

/**
 * The roster grouped by tool, in the order each kind first appears.
 *
 * A profile is an existing roster entry, never a Grove invention: the agent
 * identity sent on create stays `agent_name`, and grouping is presentation.
 */
export function agentTools(agents: readonly AgentSummaryView[]): AgentTool[] {
  const byKind = new Map<AgentKind, AgentSummaryView[]>();
  for (const agent of agents) {
    const group = byKind.get(agent.kind);
    if (group) group.push(agent);
    else byKind.set(agent.kind, [agent]);
  }
  return [...byKind].map(([kind, profiles]) => ({ kind, label: toolLabel(kind), profiles }));
}

/**
 * The entry a tool switch lands on: the one the draft already names when it
 * belongs to that tool, else the tool's first profile.
 */
export function profileForTool(tool: AgentTool, current: string | null): string | null {
  return tool.profiles.find((profile) => profile.name === current)?.name
    ?? tool.profiles[0]?.name
    ?? null;
}

/** Where the work happens: the checkout you have, or an isolated worktree. */
export type Placement = "current" | "isolated";

export function placementOf(branchMode: BranchMode): Placement {
  return branchMode === "root" ? "current" : "isolated";
}

/**
 * Who answered a field, keyed by request field name (#1093). `request` is a
 * choice made here; the rest are config layers.
 */
export type LaunchSource = NonNullable<WorkspaceDefaultsView["sources"]>[string];
export type LaunchSources = Partial<Record<"runtime" | "branch_plan" | "agent_name", LaunchSource>>;

/**
 * The provenance an untouched create would carry: the engine's `sources`, with
 * every field the person changed re-attributed to `request`. A source about a
 * field this draft overrode describes an answer that will not be used.
 */
export function draftSources(
  sources: WorkspaceDefaultsView["sources"] | undefined,
  touched: LaunchState["touched"],
): LaunchSources {
  const out: LaunchSources = {};
  for (const field of ["runtime", "branch_plan", "agent_name"] as const) {
    const draft = DRAFT_FIELD[field]!;
    if (touched.has(draft)) out[field] = "request";
    else if (sources?.[field] !== undefined) out[field] = sources[field];
  }
  return out;
}

/** The first open problem owned by `field`, as the sentence its control shows. */
export function problemFor(
  problems: readonly LaunchProblemView[],
  field: LaunchField,
): string | null {
  return problems.find((problem) => DRAFT_FIELD[problem.field] === field)?.message ?? null;
}

const SOURCE_WORDS: Readonly<Record<LaunchSource, string>> = {
  request: "Chosen here",
  project: "Project default",
  user: "Your default",
  config: "Configured",
  builtin: "Built-in default",
};

export interface LaunchSummaryInput {
  readonly values: Pick<LaunchValues, "runtime" | "branchMode" | "agentName" | "baseRef">;
  readonly projectLabel: string | null;
  readonly runtimeLabel: string | null;
  readonly sources?: LaunchSources;
}

/** Where this brief will run, split into the parts the admonition lays out. */
export interface LaunchSummaryText {
  /** "Runs on this host": the one fact a reader scans for. */
  readonly title: string;
  /** Which copy of the project, as a sentence. */
  readonly where: string;
  /** Who chose each answer, grouped by source; `null` when the engine said nothing. */
  readonly provenance: string | null;
}

/**
 * What the admonition under the shelf says about where this will run.
 *
 * Every clause is a value the draft holds; an unresolved one is left out
 * rather than guessed, and `null` comes back while there is nothing yet to say.
 * The project is the person's selection, so it never carries a source.
 * Provenance is grouped by SOURCE ("Your default: runtime, placement, agent"),
 * so the line names each source once instead of repeating it per field.
 */
export function launchSummary({
  values,
  projectLabel,
  runtimeLabel,
  sources,
}: LaunchSummaryInput): LaunchSummaryText | null {
  if (projectLabel === null || runtimeLabel === null) return null;
  const where =
    placementOf(values.branchMode) === "current"
      ? `In ${projectLabel}'s current checkout, with no new branch or worktree.`
      : `In an isolated worktree of ${projectLabel}${values.baseRef ? ` from ${values.baseRef}` : ""}.`;
  const bySource = new Map<LaunchSource, string[]>();
  const fields: readonly [string, LaunchSource | undefined][] = [
    ["runtime", sources?.runtime],
    ["placement", sources?.branch_plan],
    ["agent", sources?.agent_name],
  ];
  for (const [field, source] of fields) {
    if (source !== undefined) bySource.set(source, [...(bySource.get(source) ?? []), field]);
  }
  const provenance = [...bySource]
    .map(([source, named]) => `${SOURCE_WORDS[source]}: ${named.join(", ")}`)
    .join(" · ");
  return { title: `Runs on ${runtimeLabel}`, where, provenance: provenance === "" ? null : provenance };
}


/**
 * Which section of the Runtime picker a row sits in, in display order:
 * what can be chosen now, what this host cannot run, what nobody set up.
 */
export type RuntimeGroup = "available" | "unavailable" | "setup";

export const RUNTIME_GROUP_LABEL: Readonly<Record<RuntimeGroup, string>> = {
  available: "Available",
  unavailable: "Unavailable here",
  setup: "Not set up",
};

const GROUP_ORDER: readonly RuntimeGroup[] = ["available", "unavailable", "setup"];

/** One row of the Runtime picker, as the engine described it. */
export interface RuntimeRow {
  /** The picker id: unique per row, because a node repeats `host`/`container`. */
  readonly key: string;
  /** What a selection writes: the request's runtime choice and its node. */
  readonly runtime: string;
  readonly node: string | null;
  readonly kind: RuntimeOption["kind"];
  readonly label: string;
  readonly group: RuntimeGroup;
  /** Why it cannot be picked, or its setup step; `null` when selectable. */
  readonly unavailable: string | null;
  /** The line under a selectable row's name, when it has something to say. */
  readonly note: string | null;
}

/**
 * The runtime KIND a draft's choice names. Host and container are themselves;
 * any other value is a `runtimes.<name>` block, which is remote. (`===` cannot
 * narrow `RuntimeChoice`'s `string & {}`, so this is a function, not a cast.)
 */
export function runtimeKindOf(choice: string): RuntimeOption["kind"] {
  return choice === "host" ? "host" : choice === "container" ? "container" : "remote";
}

/**
 * A row's name, never a config keyword: host and container take the shared
 * runtime word; a configured remote is the `runtimes.<name>` block its owner
 * named; an unconfigured driver is the engine's `driver_label` for it.
 */
export function runtimeOptionLabel(
  option: Pick<RuntimeOption, "id" | "kind" | "driver_label">,
  kindLabel: (kind: RuntimeOption["kind"]) => string,
): string {
  if (option.kind !== "remote") return kindLabel(option.kind);
  return option.id.startsWith("driver:") ? option.driver_label ?? kindLabel("remote") : option.id;
}

/** The picker key for a runtime on a node; `::` cannot occur in either part. */
export function runtimeKey(runtime: string, node: string | null): string {
  return node === null ? runtime : `${node}::${runtime}`;
}

/**
 * The engine's runtime inventory as picker rows for the selected node, grouped
 * available → unavailable → not set up, engine order kept inside each group.
 *
 * Episodic (task-only) blocks are dropped outright — they can never be a
 * workspace. Unconfigured drivers stay visible but unselectable with their
 * setup step, so an implemented provider is discoverable without pretending
 * it is configured. Nothing here claims readiness: `unknown` (every remote, and
 * every hub node) is selectable and says only that nothing measured it.
 */
export function runtimeRows(
  options: readonly RuntimeOption[],
  node: string | null,
  kindLabel: (kind: RuntimeOption["kind"]) => string,
): RuntimeRow[] {
  const rows = options
    .filter((option) => option.workspace_target && option.node === node)
    .map((option): RuntimeRow => {
      const group: RuntimeGroup =
        option.status === "not_configured"
          ? "setup"
          : option.status === "unavailable"
            ? "unavailable"
            : "available";
      const unavailable =
        group === "setup"
          ? option.setup ?? option.reason ?? "Not configured"
          : group === "unavailable"
            ? option.reason ?? "Unavailable"
            : null;
      // A configured block is named by its owner, so its driver is what the
      // second line adds; "unknown" is the honest word for what nobody probed.
      const kind = option.id.startsWith("driver:") ? null : option.driver_label;
      const unchecked = option.status === "unknown" ? "readiness not checked" : null;
      const note = [kind, unchecked].filter(Boolean).join(" · ");
      return {
        key: runtimeKey(option.id, option.node),
        runtime: option.id,
        node: option.node,
        kind: option.kind,
        label: runtimeOptionLabel(option, kindLabel),
        group,
        unavailable,
        note: group === "available" && note !== "" ? note.charAt(0).toUpperCase() + note.slice(1) : null,
      };
    });
  return GROUP_ORDER.flatMap((group) => rows.filter((row) => row.group === group));
}

/**
 * Why this roster entry cannot run on the chosen runtime, from the entry's own
 * `runtimes` — or `null`. Presentation only: the preview's problem is what
 * refuses Send, so this never blocks on its own.
 */
export function profileConflict(
  agent: Pick<AgentSummaryView, "name" | "runtimes">,
  runtimeKind: RuntimeOption["kind"] | null,
): string | null {
  if (runtimeKind === null || agent.runtimes.includes(runtimeKind)) return null;
  return `${agent.name} cannot run on a ${runtimeKind} runtime`;
}

/**
 * An existing workspace's runtime as it actually is: a remote one names the
 * `runtimes.<name>` block it runs on, because "Remote" alone does not say where.
 */
export function workspaceRuntimeLabel(
  state: { readonly runtime: RuntimeOption["kind"]; readonly remote_runtime?: string | null },
  kindLabel: (kind: RuntimeOption["kind"]) => string,
): string {
  return state.runtime === "remote" && state.remote_runtime
    ? `${kindLabel("remote")} · ${state.remote_runtime}`
    : kindLabel(state.runtime);
}
