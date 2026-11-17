import type {
  BranchPlan,
  LaunchPreviewView,
  LaunchProblemView,
  CreateWorkspaceRequest,
  TicketSelector,
  WorkspaceDefaultsView,
} from "@/lib/grove/api";
import type {
  LaunchField,
  LaunchState,
  LaunchTicket,
  LaunchValues,
} from "@/components/grove/launch/launch-state";
import {
  attachmentCountError,
  attachmentError,
  type StagedAttachment,
} from "./attachments";

const MAX_TITLE_LENGTH = 120;
const MAX_PROMPT_LENGTH = 10_000;
const MAX_MODEL_LENGTH = 64;

/**
 * Why Send must wait, or `null` when the draft can be built honestly.
 *
 * Only the project. Every other untouched field — agent and placement included
 * — is OMITTED and the engine's resolver answers it at create, so a send made
 * before `GET /defaults` returns gets exactly what that response would have
 * shown. (Before #1093 the request had to carry `branch_plan`, and Send waited
 * for THIS project's defaults so it could not pin the placeholder.)
 */
export function launchBlocker(state: Pick<LaunchState, "values">): string | null {
  return state.values.repoRoot === null ? "Choose a project first" : null;
}

/**
 * Validate a manually entered model id before the composer sends it.
 *
 * This only gives immediate, local feedback. The Python wire contract remains
 * the authoritative validation boundary for every client and direct API call.
 */
export function customModelError(value: string): string | null {
  const model = value.trim();
  if (model === "") return "Enter a custom model id.";
  if (model.length > MAX_MODEL_LENGTH) {
    return `A custom model id must be at most ${MAX_MODEL_LENGTH} characters.`;
  }
  // Kept ahead of the pattern purely for the better message; the pattern's
  // leading-alphanumeric anchor already refuses it.
  if (model.startsWith("-")) return "A custom model id cannot start with a dash.";
  // Mirrors `_MODEL_ID_PATTERN` in src/grove/core/contracts/requests.py.
  // The brackets are NOT an oversight and must not be "tidied" out again:
  // `anthropic-opus-5[1m]` is a real, current gateway id, and half of one
  // measured 22-model catalog takes that form. Narrowing this rejects a value
  // the wire contract accepts, which is the worst direction for a client
  // check — the user is refused with no way to discover why.
  return /^[A-Za-z0-9][A-Za-z0-9._/:[\]-]*$/.test(model)
    ? null
    : "Use ASCII letters, numbers, and - . _ / : [ ] only.";
}

/**
 * The saved model an untouched create of `agentName` will launch with.
 *
 * Mirrors `GroveConfig.default_model_for`, which decides it: a model saved for
 * the saved agent is not another roster entry's, so another entry shows the
 * agent's own default. With no saved agent the saved model stays global.
 */
export function inheritedModel(
  defaults: Pick<WorkspaceDefaultsView, "agent" | "model"> | undefined,
  agentName: string | null,
): string | null {
  if (!defaults?.model) return null;
  if (defaults.agent && defaults.agent !== agentName) return null;
  return defaults.model;
}

/**
 * The word a picker shows for one reasoning-effort level.
 *
 * The wire carries the tool's own spelling (`xhigh`), which is what gets sent
 * back; only the display changes, and only for the one level that is not a
 * word on its own.
 */
export function effortLabel(effort: string): string {
  if (effort === "xhigh") return "Extra high";
  return effort.charAt(0).toUpperCase() + effort.slice(1);
}

/** Convert the form's branch vocabulary into the engine's discriminated wire shape. */
export function branchPlanFor(values: Pick<LaunchValues, "branchMode" | "branchName" | "existingBranch" | "remoteRef" | "localName" | "baseRef">): BranchPlan {
  const baseRef = values.baseRef ?? "HEAD";

  switch (values.branchMode) {
    case "auto":
      return { kind: "auto", base_ref: baseRef };
    case "new":
      return { kind: "new_named", name: values.branchName, base_ref: baseRef };
    case "existing":
      return { kind: "existing_local", name: values.existingBranch };
    case "remote":
      return {
        kind: "track_remote",
        remote_ref: values.remoteRef,
        ...(values.localName ? { local_name: values.localName } : {}),
      };
    case "root":
      return { kind: "root" };
  }
}

/**
 * Which draft field answers each request field, so an engine verdict about a
 * request field lands on the control that owns it. Only the fields a create
 * form edits; an unmapped field's problem is shown at the form level instead.
 */
export const DRAFT_FIELD: Readonly<Record<string, LaunchField>> = {
  agent_name: "agentName",
  agent_kind: "agentKind",
  runtime: "runtime",
  remote_runtime: "runtime",
  node: "node",
  branch_plan: "branchMode",
  model: "model",
  effort: "effort",
  native: "native",
  project_cwd: "projectCwd",
  brief: "brief",
  skip_init: "skipInit",
};

/**
 * The engine's refusals that apply to the draft as it stands.
 *
 * A preview of the CURRENT launch request is the whole answer: it saw every
 * touched value, so its problems need no filtering. Before one exists (or while
 * it describes an older draft), the seed's problems apply — they describe the
 * untouched create, so a field the person changed is no longer theirs to judge.
 */
export function openProblems(
  state: Pick<LaunchState, "values" | "touched" | "problems" | "preview" | "refusal">,
): LaunchProblemView[] {
  // The create's own refusal of this exact request outranks every prediction
  // about it: the engine has already said no to it, live.
  const key = previewKey(state);
  if (state.refusal !== undefined && state.refusal.request === key) return [state.refusal.problem];
  const preview = currentPreview(state);
  if (preview) return [...preview.problems];
  return (state.problems ?? []).filter((problem) => {
    const draft = DRAFT_FIELD[problem.field];
    return draft === undefined || !state.touched.has(draft);
  });
}

/**
 * The sentence that disarms Send for this draft, or `null`.
 *
 * Shown at FORM level on both surfaces whatever field it names: the pill that
 * owns a field may be hidden (no branch pill for the current checkout) or may
 * not exist (`node`, `brief`), and a
 * refusal nobody can read is a Send button that does nothing. Pills
 * additionally mark their own field; this is the fallback that cannot hide.
 */
export function launchVerdict(
  state: Pick<LaunchState, "values" | "touched" | "problems" | "preview" | "refusal">,
): string | null {
  // Not `launchBlocker`: a missing project is the page's own loading state, not
  // a refusal, and a red sentence on a fresh page would be one nobody earned.
  return openProblems(state)[0]?.message ?? null;
}

/** The engine's preview of THIS draft, or `null` while none describes it. */
export function currentPreview(
  state: Pick<LaunchState, "values" | "touched" | "preview">,
): LaunchPreviewView | null {
  return state.preview !== undefined && state.preview.request === previewKey(state)
    ? state.preview.view
    : null;
}

/**
 * The identity of a draft's launch half, or `null` when it cannot be built.
 * Serialised, so two drafts that would send the same request share a key.
 */
export function previewKey(state: Pick<LaunchState, "values" | "touched">): string | null {
  try {
    return JSON.stringify(launchRequest(state as LaunchState));
  } catch {
    return null;
  }
}

/**
 * The wire pair one runtime choice means: host and container name a runtime,
 * any other value names a remote block and implies `remote`. The engine's
 * `CreateWorkspaceRequest.runtime_fields` is the same rule; whether the block
 * exists is the engine's refusal, never this adapter's.
 */
export function runtimeFields(
  choice: string,
): Pick<CreateWorkspaceRequest, "runtime" | "remote_runtime"> {
  if (choice === "host" || choice === "container") return { runtime: choice };
  return { runtime: "remote", remote_runtime: choice };
}

/**
 * The launch half of a create: every field that decides WHAT runs WHERE, and
 * none of what the agent is told (prompt, title, files). This is the body the
 * preview checks, so the preview and the create can never disagree about a
 * launch field — they are the same object.
 *
 * Untouched fields are omitted so the engine's resolver answers them.
 */
export function launchRequest(state: LaunchState): CreateWorkspaceRequest {
  const { values, touched } = state;
  if (!values.repoRoot) throw new RangeError("Choose a project before creating a workspace");
  if (values.model && values.model.length > MAX_MODEL_LENGTH) {
    throw new RangeError("Model must be at most 64 characters");
  }
  // The client check gives the form an actionable refusal. The wire contract is
  // still authoritative for direct API callers and every other Grove client.
  if (values.customModel) {
    const error = customModelError(values.model ?? "");
    if (error) throw new RangeError(error);
  }

  const request: CreateWorkspaceRequest = { repo_root: values.repoRoot };
  // A Profile pick names one roster entry; an Agent pick names only the tool,
  // so the engine can choose its runtime-compatible profile.
  if (touched.has("agentName") && values.agentName) request.agent_name = values.agentName;
  else if (touched.has("agentKind") && values.agentKind) request.agent_kind = values.agentKind;
  if (touched.has("model")) {
    request.model = values.customModel ? values.model?.trim() ?? null : values.model;
  }
  // Omitted unless chosen, so the tool's own default applies; there is no
  // saved effort default for the engine to resolve instead.
  if (values.effort !== null) request.effort = values.effort;
  if (values.ticket) request.ticket = ticketFor(values.ticket);

  // Omitting project_cwd delegates to the engine's `agent_cwds.default`. An
  // explicit pill choice wins; a nested project's own cwd is otherwise its
  // identity, not a form default.
  if (touched.has("projectCwd")) {
    request.project_cwd = values.projectCwd;
  } else if (values.selectedProjectCwd !== values.repoRoot) {
    request.project_cwd = values.selectedProjectCwd;
  }
  if (touched.has("runtime") && values.runtime !== null) {
    Object.assign(request, runtimeFields(values.runtime));
  }
  if (touched.has("node")) request.node = values.node;
  if (touched.has("brief")) request.brief = values.brief;
  if (touched.has("native")) request.native = values.native;
  if (touched.has("skipInit")) request.skip_init = values.skipInit;
  // Placement follows the touched rule like every other field: omitted, the
  // engine resolves the saved branch mode (or the current checkout). A saved
  // `new`/`existing`/`remote` mode names no branch, so the engine reports it as
  // a problem and the draft asks for the branch instead of guessing one.
  if (touched.has("branchMode")) request.branch_plan = branchPlanFor(values);
  return request;
}

/**
 * The one create payload: the launch half plus what the agent is told.
 *
 * It refuses, with the engine's own sentence, while any problem the engine
 * reported for this draft is still open — sending it would only move that
 * sentence to after a round trip, and the draft is kept either way.
 *
 * `attachments` is the composer's staged file list, the ONE input that is not a
 * control value — it rides the create because the workspace it would otherwise
 * be uploaded to does not exist yet.
 */
export function buildCreateRequest(
  state: LaunchState,
  prompt: string,
  attachments: readonly StagedAttachment[] = [],
): CreateWorkspaceRequest {
  const request = launchRequest(state);
  const refused = openProblems(state)[0];
  if (refused) throw new RangeError(refused.message);
  // An untyped title is OMITTED, never derived from the prompt: the engine names
  // the workspace `wk-` plus random hex (the one rule every client shares) and
  // asks the agent to rename it once it knows the task.
  const title = state.values.titleOverride?.trim() || undefined;
  if (title && title.length > MAX_TITLE_LENGTH) throw new RangeError("Workspace title must be at most 120 characters");
  if (prompt.length > MAX_PROMPT_LENGTH) throw new RangeError("Prompt must be at most 10,000 characters");
  // Both checks also run at the picker, where they cost nothing and answer
  // immediately. They are repeated here because this is the single request
  // builder: a file list assembled any other way still cannot get past it.
  const tooMany = attachmentCountError(attachments.length);
  if (tooMany) throw new RangeError(tooMany);
  for (const file of attachments) {
    const refusal = attachmentError(file);
    if (refusal) throw new RangeError(refusal);
  }

  request.initial_prompt = prompt;
  if (title) request.title = title;
  if (attachments.length > 0) request.attachments = attachmentsFor(attachments);
  return request;
}

/**
 * The staged files as the create contract takes them. An empty list is OMITTED
 * by the caller rather than sent as `[]`, so a create with nothing staged is
 * byte-identical to the one this surface has always built. `size` is dropped:
 * the create contract forbids fields it does not declare.
 */
function attachmentsFor(attachments: readonly StagedAttachment[]): CreateWorkspaceRequest["attachments"] {
  return attachments.map(({ name, content_base64 }) => ({ name, content_base64 }));
}

function ticketFor(ticket: LaunchTicket): TicketSelector {
  return { provider: ticket.provider as TicketSelector["provider"], id: ticket.id, kind: "issue" };
}
