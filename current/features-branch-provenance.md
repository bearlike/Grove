# Branch provenance and ownership

Grove distinguishes between branches it created and branches you
attached. The distinction shapes one decision: when you kill a workspace,
should the branch go too? The default answer is yes if Grove made the
branch, no if it was already yours.

## Four branch sources

The create modal asks where the workspace's branch comes from. The
choice is a Pydantic discriminated union with four variants:

| Variant | What it means | Provenance |
|---|---|---|
| **Auto** | Grove names the branch from your title (`<branch_prefix><slug>`). | `GROVE_CREATED` |
| **New named** | You type the branch name. Grove creates it from `HEAD`. | `GROVE_CREATED` |
| **Existing local** | Pick from the repo's existing local branches. | `USER_ATTACHED` |
| **Track remote** | Pick a remote-only branch. Grove creates a tracking local branch. | `USER_ATTACHED` |

The variant lives in `grove.core.contracts.branch_plan` as a tagged
union (`kind: Literal["auto" | "new_named" | "existing_local" | "track_remote"]`).
Every variant carries its own fields. The JSON Schema generated for the
union has a typed shape per variant. The create modal mounts one form per
variant in the DOM with the inactive ones hidden, so values persist as
you switch.

## `GROVE_CREATED` vs `USER_ATTACHED`

Each workspace state record carries a `branch_provenance` field with one
of two values:

- **`GROVE_CREATED`**: Grove ran `git branch <name>` (Auto, New named).
- **`USER_ATTACHED`**: Grove ran `git worktree add` against a branch that
  already existed locally (Existing local), or created a tracking local
  branch from a remote ref (Track remote). Either way the branch
  pre-existed Grove's involvement.

Provenance persists in the workspace state file. Pause and resume cycles
preserve it. Only `kill` consults it.

## What `kill` actually does

`kill(workspace_id, *, delete_branch=None)` is the only lifecycle
operation that ever deletes a branch. The flag has three meanings:

| `delete_branch` | Behavior |
|---|---|
| `None` (default) | Resolve from `branch_provenance`: `GROVE_CREATED → True`, `USER_ATTACHED → False`. |
| `True`  | Force-delete the local branch regardless of provenance. |
| `False` | Keep the local branch regardless of provenance. |

The TUI's kill modal flips this with a checkbox. The checkbox default is
whatever `delete_branch=None` would resolve to. You can override either
way before confirming.

## Why no remote deletion

There is no `delete_remote` flag. There is no tmux key for it. There is
no CLI subcommand that pushes a delete. Deleting a remote branch requires
push credentials, runs through whatever branch protections the remote
enforces, and is the kind of action that wants to go through your team's
normal git workflow.

To delete a remote, run `git push --delete origin <branch>`, use your CI
workflow, or use your code-review tool's UI. Grove leaves remote
deletion alone.

## The kill event

When a kill completes, Grove emits a `WorkspaceEvent` with details
including `branch_deleted: "true" | "false"`. Future clients (web, MCP)
can flash the right message ("branch retained" vs "branch
removed") without re-deriving the rule. The event is the contract; the
boolean is the truth.

## See also

- [Workspace lifecycle](features-workspace-lifecycle.md): when each operation applies.
- [Status semantics](features-status.md): what ORPHANED actually means and why kill is the only path out.
- [TUI tour](use-tui.md): the create modal and the kill confirmation modal.
