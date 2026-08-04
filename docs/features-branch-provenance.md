# Branch provenance and ownership

## Who owns each branch

Grove distinguishes branches it created from branches you attached.
That decides one thing: when you kill a workspace, does the branch go
too? Yes if Grove made it, no if it was already yours. Your git stays
yours.

## Five branch sources

The create modal asks where the branch comes from, a Pydantic
discriminated union with five variants:

| Variant | What Grove does | Provenance |
|---|---|---|
| **Auto** | Names the branch from your title (`<branch_prefix><slug>-<ts>`), off `HEAD`. | `GROVE_CREATED` |
| **New named** | You type the name. Grove creates it off `HEAD`. | `GROVE_CREATED` |
| **Existing local** | Checks out an existing local branch. | `USER_ATTACHED` |
| **Track remote** | Creates a fresh local branch tracking a remote-only one. The remote ref keeps every commit. | `GROVE_CREATED` |
| **Root** | No worktree, no new branch: runs in the repo root on the branch already checked out. | `USER_ATTACHED` |

The union lives in `grove.core.contracts.branch_plan`, tagged by `kind`.
The modal mounts one form per variant and hides the inactive ones, so
values persist as you switch.

Root is a placement choice, not a branch choice. It adopts your live
checkout, so kill never deletes its branch, even asked explicitly. Full
behavior is on the
[workspace lifecycle](features-workspace-lifecycle.md#root-workspaces) page.

## `GROVE_CREATED` vs `USER_ATTACHED`

Each workspace state record carries a `branch_provenance` field with one
of two values.

- **`GROVE_CREATED`**: Grove created the local branch, so kill deletes it by default.
- **`USER_ATTACHED`**: the branch pre-existed Grove, so kill keeps it.

Provenance persists across pause and resume. Only `kill` consults it.

## What `kill` actually does

`kill(workspace_id, *, delete_branch=None)` is the only lifecycle
operation that deletes a branch.

| `delete_branch` | Behavior |
|---|---|
| `None` (default) | Resolve from `branch_provenance`: `GROVE_CREATED → True`, `USER_ATTACHED → False`. |
| `True` (`--delete-branch`) | Force-delete the local branch, except Root workspaces, where the flag is overridden to `False`. |
| `False` (`--keep-branch`) | Keep the local branch regardless of provenance. |

The TUI's kill modal flips this with a checkbox, defaulting to what
`delete_branch=None` resolves to, overridable before confirming.

## Why no remote deletion

There is no `delete_remote` flag, no tmux key, no CLI subcommand that
pushes a delete. Deleting a remote branch needs push credentials and
runs through the remote's branch protections, so it belongs in your
team's normal git workflow. Run `git push --delete origin <branch>`, use
CI, or use your code-review tool's UI.

## The kill event

A completed kill emits a `WorkspaceEvent` carrying
`branch_deleted: "true" | "false"`, the outcome rather than the request.
Any client can flash the right message without re-deriving the rule.

## See also

- [Workspace lifecycle](features-workspace-lifecycle.md): when each operation applies.
- [Status semantics](features-status.md): what ORPHANED means and why kill is the only path out.
- [TUI tour](use-tui.md): the create and kill confirmation modals.
