import type { BranchInfo, CreateWorkspaceRequest } from "@/lib/grove/api";

type BranchPlan = NonNullable<CreateWorkspaceRequest["branch_plan"]>;

/**
 * A branch choice as one string, so the whole plan fits in a single `Select`.
 *
 * The daemon's `BranchPlan` is a five-member discriminated union, which is the
 * right shape on the wire and the wrong shape for a form: a picker per variant
 * would put five controls on screen to express one decision. Encoding the
 * choice as `auto` / `new` / `local:<name>` / `remote:<ref>` collapses it back
 * to one, and `toBranchPlan` is the only place that knows the encoding.
 */
export const AUTO_BRANCH = "auto";
export const NEW_BRANCH = "new";
export const ROOT_BRANCH = "root";

export function branchOptionValue(branch: BranchInfo): string {
  return `${branch.kind}:${branch.name}`;
}

/**
 * The wire plan for a form choice, or `undefined` to let the daemon decide.
 *
 * **`root` used to be deliberately unreachable here**, on the reasoning that
 * running an agent in the repo root defeats the isolation Grove provides. That
 * withheld a capability the engine fully supports and the contract names as
 * "the escape hatch for users who don't want an isolated worktree per task"
 * (`RootBranch`) — so the client was overriding a product decision the engine
 * had already made, and the one workflow it blocked (work in place on what you
 * already have checked out) had no other route.
 *
 * The honest arrangement is the one the engine uses: offer it, and state its
 * cost where it is chosen. Root creates no worktree and no branch, supports
 * neither pause nor resume, and `kill` refuses to delete the branch whatever
 * the caller asks. Hiding a mode is not the same as explaining it.
 */
export function toBranchPlan(choice: string, newName: string): BranchPlan | undefined {
  if (choice === AUTO_BRANCH) return undefined;
  if (choice === ROOT_BRANCH) return { kind: "root" };
  if (choice === NEW_BRANCH) {
    const name = newName.trim();
    return name === "" ? undefined : { kind: "new_named", name, base_ref: "HEAD" };
  }
  const separator = choice.indexOf(":");
  const kind = choice.slice(0, separator);
  const name = choice.slice(separator + 1);
  if (kind === "local") return { kind: "existing_local", name };
  if (kind === "remote") return { kind: "track_remote", remote_ref: name };
  return undefined;
}
