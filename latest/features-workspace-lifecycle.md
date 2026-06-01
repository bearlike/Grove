# Workspace lifecycle

Every Grove workspace passes through the same small set of operations.
This page covers what each one touches and what each one deliberately
does not.

<figure class="grove-shot" markdown>
  <span class="grove-shot__frame">
    ![Grove TUI showing four workspaces in mixed lifecycle states](img/screenshots/tui-list.png)
  </span>
  <p class="grove-shot__body">Four workspaces, four lifecycle moments. Active and idle on top. The offline row offers <code>o</code> (respawn) and <code>k</code> (kill).</p>
</figure>

## Four operations and a recovery path

```mermaid
stateDiagram-v2
    [*] --> create : n
    create --> ACTIVE
    ACTIVE --> IDLE : quiet
    IDLE --> ACTIVE : output
    ACTIVE --> PAUSED : pause (p)
    IDLE --> PAUSED : pause (p)
    PAUSED --> ACTIVE : resume (R)
    ACTIVE --> OFFLINE : tmux vanishes
    IDLE --> OFFLINE : tmux vanishes
    OFFLINE --> ACTIVE : respawn (o)
    ACTIVE --> ORPHANED : worktree gone
    OFFLINE --> ORPHANED : worktree gone
    PAUSED --> [*] : kill (k)
    ACTIVE --> [*] : kill (k)
    IDLE --> [*] : kill (k)
    OFFLINE --> [*] : kill (k)
    ORPHANED --> [*] : kill (k)
```

| Op | Branch | Worktree | tmux session | Init script |
|---|---|---|---|---|
| **create** (`n`)  | created or attached | created | created | runs if `enabled: true` |
| **pause** (`p`)   | kept | **removed** | killed | n/a |
| **resume** (`R`)  | kept | recreated from branch | recreated | re-runs only if `run_on_resume: true` |
| **kill** (`k`)    | deleted (Grove-created default; user-attached default keeps it) | removed | killed | n/a |
| **respawn** (`o`) | kept | **must exist** | recreated | not re-run by default |

`create`, `pause`, `resume`, and `kill` are the four lifecycle verbs the
operator drives directly. `respawn` is a recovery path for the specific
case where the tmux session vanished externally but the worktree is
intact.

## Why pause refuses dirty worktrees

`pause` removes the worktree, which would silently lose any uncommitted
work. Grove refuses. The lifecycle method raises a typed
`DirtyWorktreeError`. The TUI surfaces a flash message that names the
dirty paths. The fix is yours: commit, stash, or push, then pause.

This is the same principle that keeps Grove out of `git commit` and
`git push`. The user owns the code's lifecycle, and Grove owns the
workspace's lifecycle. The two sit alongside each other and do not
share a verb.

## Why kill never touches remotes

`kill` deletes the local branch by default when Grove created it. It
does not touch remotes. There is no flag that opts in. Remote branches
are deleted with `git push --delete`, which uses your push credentials
and your access policy. That belongs in your shell, with the rest of the
team-policy machinery (CI, branch protection, code review) that Grove
sits below.

The provenance details that decide which local branch gets deleted by
default live in [branch provenance](features-branch-provenance.md).

## Recovery from a vanished session

A tmux session can disappear without notice. A terminal restarts, a host
reboots, or someone runs `tmux kill-server`. The worktree on disk does
not move; it stays exactly where it was.

When this happens, Grove's reconciler promotes the workspace from
RUNNING to OFFLINE on the next list refresh. The footer offers exactly
two keys: `o` (respawn) and `k` (kill). `respawn` rebuilds the tmux
session from scratch with the same windows and the same agent command.
The workspace returns to ACTIVE.

If the worktree directory is also gone (someone deleted it manually),
the workspace is ORPHANED. There is nothing to respawn against. `kill`
is the only path forward.

## Side effects live at the edges

Grove's manager reads no config file directly and shells out to nothing.
Two modules carry every side effect. `grove/core/git.py` wraps the five
`git` subcommands the lifecycle needs. `grove/core/tmux.py` wraps
`libtmux`. Everything else (branch resolution, cascade merging, state
reconciliation, init-outcome capture) is pure logic that runs against
in-memory data.

The manager is testable without git or tmux binaries. The side-effect
modules are testable with real binaries behind an integration marker.
New I/O concerns belong in those two files, or a third side-effect
module. They should not be scattered across the codebase. See
[architecture](develop-architecture.md) for the full boundary diagram.
