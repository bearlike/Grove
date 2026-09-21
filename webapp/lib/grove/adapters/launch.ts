import { Md5 } from "ts-md5";

import type { BranchPlan, CreateWorkspaceRequest, TicketSelector } from "@/lib/grove/api";
import type { LaunchState, LaunchTicket, LaunchValues } from "@/components/grove/launch/launch-state";
import {
  attachmentCountError,
  attachmentError,
  type StagedAttachment,
} from "./attachments";

const MAX_TITLE_LENGTH = 120;
const MAX_PROMPT_LENGTH = 10_000;
const MAX_MODEL_LENGTH = 64;

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

/** How much of the digest is shown. Nine hex chars is 36 bits — far past any
 * plausible collision across one host's workspaces, and short enough to sit in
 * a rail row without truncating. */
const TITLE_DIGEST_LENGTH = 9;

/**
 * A letter ahead of the digest, so the title can NEVER be a bare digit run.
 *
 * The title becomes the auto branch's slug, and the numeric ticket providers
 * claim a leading digit run at the head of a branch as an issue id. About one
 * brief in 110 digests to all digits, and one such workspace shipped attached
 * to a phantom issue with a phase claim seeded and recorded for it. The prefix
 * is the fix at the smallest seam: the branch grammar is right to read
 * `123-fix` as #123, and a machine handle simply must not look like one.
 */
const TITLE_DIGEST_PREFIX = "t";

/**
 * The default workspace name: a digest of the brief, not a slice of it.
 *
 * A first line makes a decent title only when the first line happens to be a
 * summary; for a pasted stack trace, a wrapped paragraph or a sentence that
 * starts with "So I was thinking", it produces a name nobody would have
 * written. A digest is at least honest about being a machine-assigned handle,
 * it is stable for identical briefs, and it slugs cleanly into a worktree path.
 *
 * **This is a DEFAULT, and it is only defensible because renaming is one
 * right-click away** — the rail's own menu writes `title`/`description` through
 * `PATCH /workspaces/{id}`. If that affordance ever goes, this should go with
 * it: an unreadable name with no way to change it is not a design.
 *
 * MD5 rather than Web Crypto: `crypto.subtle` deliberately omits MD5, and its
 * digest API is async, which this cannot be — the composer renders the derived
 * name live under the input as you type. Nothing here is a security boundary;
 * it is a short stable handle.
 */
export function deriveTitle(prompt: string): string {
  const text = prompt.trim();
  if (!text) return "Untitled task";
  return TITLE_DIGEST_PREFIX + Md5.hashStr(text).slice(0, TITLE_DIGEST_LENGTH);
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
 * Build the one create payload from the form's display state and the composer text.
 *
 * Values seeded from the defaults read describe the cascade's current answer, not a
 * user override. Only a touched field belongs in this request, so create resolves
 * untouched fields at the moment it runs.
 *
 * `attachments` is the composer's staged file list, and it is the ONE input here
 * that is not a control value — it rides the create because the workspace it
 * would otherwise be uploaded to does not exist yet.
 */
export function buildCreateRequest(
  state: LaunchState,
  prompt: string,
  attachments: readonly StagedAttachment[] = [],
): CreateWorkspaceRequest {
  const { values, touched } = state;
  const title = values.titleOverride ?? deriveTitle(prompt);
  if (!values.agentName) throw new RangeError("Choose an agent before creating a workspace");
  if (!values.repoRoot) throw new RangeError("Choose a project before creating a workspace");
  if (!title) throw new RangeError("Workspace title must not be empty");
  if (title.length > MAX_TITLE_LENGTH) throw new RangeError("Workspace title must be at most 120 characters");
  if (prompt.length > MAX_PROMPT_LENGTH) throw new RangeError("Prompt must be at most 10,000 characters");
  if (values.model && values.model.length > MAX_MODEL_LENGTH) {
    throw new RangeError("Model must be at most 64 characters");
  }
  // The client check gives the form an actionable refusal. The wire contract is
  // still authoritative for direct API callers and every other Grove client.
  if (values.customModel) {
    const error = customModelError(values.model ?? "");
    if (error) throw new RangeError(error);
  }
  // Both checks also run at the picker, where they cost nothing and answer
  // immediately. They are repeated here because this is the single request
  // builder: a file list assembled any other way still cannot get past it.
  const tooMany = attachmentCountError(attachments.length);
  if (tooMany) throw new RangeError(tooMany);
  for (const file of attachments) {
    const refusal = attachmentError(file);
    if (refusal) throw new RangeError(refusal);
  }

  const request: Partial<CreateWorkspaceRequest> = {
    repo_root: values.repoRoot,
    agent_name: values.agentName,
    title,
    initial_prompt: prompt,
  };

  // Unlike branch_plan, omitting project_cwd really does delegate to the engine:
  // its cascade resolves agent_cwds.default. An explicit pill choice wins; a
  // nested project's own cwd is otherwise its identity, not a form default.
  if (touched.has("projectCwd")) {
    request.project_cwd = values.projectCwd;
  } else if (values.selectedProjectCwd !== values.repoRoot) {
    request.project_cwd = values.selectedProjectCwd;
  }
  if (touched.has("model")) {
    request.model = values.customModel ? values.model?.trim() ?? null : values.model;
  }
  if (touched.has("runtime")) request.runtime = values.runtime;
  if (touched.has("brief")) request.brief = values.brief;
  if (touched.has("native")) request.native = values.native;
  if (touched.has("skipInit")) request.skip_init = values.skipInit;
  // branch_plan is the ONE field that does not follow the touched rule, and it
  // has to break it to stay honest. For model/runtime/brief/skip_init, omitting
  // the field means "resolve it from the cascade at create time" — which the
  // ENGINE now does, reading the same saved `defaults` that `GET /defaults`
  // reports, so an untouched pill showing the cascade's answer and sending
  // nothing agree with each other. (They did not until then: the engine read
  // `container.enabled` while this surface displayed `defaults.runtime`, so a
  // saved `host` default produced a container on every project that had not
  // turned containers off.)
  //
  // branch_plan still has no such symmetry: the request defaults it to
  // AutoBranch(), so an omitted plan is indistinguishable from an explicit auto
  // one and there is no "unspecified" for the engine to resolve. Omitting it
  // means "force auto", and a user whose saved default is `root` would watch
  // the pill say Work in place while Grove quietly cut a branch. Send what the
  // pill displays.
  request.branch_plan = branchPlanFor(values);
  if (values.ticket) request.ticket = ticketFor(values.ticket);
  // An empty list is OMITTED rather than sent as `[]`, so a create with nothing
  // staged is byte-identical to the one this surface has always built. `size` is
  // dropped with it: the create contract forbids fields it does not declare.
  if (attachments.length > 0) {
    request.attachments = attachments.map(({ name, content_base64 }) => ({
      name,
      content_base64,
    }));
  }

  return request as CreateWorkspaceRequest;
}

function ticketFor(ticket: LaunchTicket): TicketSelector {
  return { provider: ticket.provider as TicketSelector["provider"], id: ticket.id, kind: "issue" };
}
