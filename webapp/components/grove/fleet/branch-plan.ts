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

export function branchOptionValue(branch: BranchInfo): string {
  return `${branch.kind}:${branch.name}`;
}

/**
 * The wire plan for a form choice, or `undefined` to let the daemon decide.
 *
 * `root` is deliberately unreachable here: running an agent directly in the
 * repo root rather than a worktree defeats the isolation Grove exists to
 * provide, and nothing in the dashboard should make it a one-click option.
 */
export function toBranchPlan(choice: string, newName: string): BranchPlan | undefined {
  if (choice === AUTO_BRANCH) return undefined;
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
