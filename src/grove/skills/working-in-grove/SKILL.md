---
name: working-in-grove
description: Use when you are the coding agent running INSIDE a Grove workspace — a git worktree Grove created for one task, on the host or in a container. Triggers on: reporting or updating your task phase or progress, GROVE_PHASE_FILE or .grove/phase.json, `grove phase`, grove_set_workspace_phase, naming this workspace or setting its title or description, keeping an attached issue or PR up to date, being asked how far along you are, or noticing you are in a worktree with a phase file. Covers the six phase names and when to write each, that your phase, note, todo checklist and activity line are PUBLISHED onto every attached ticket as a live comment, giving each attached ticket its own phase plus a `blocked` flag for work you cannot finish, keeping the public checklist current, naming a workspace somebody can find in the fleet, and attaching the pull request you opened so the work is seen to land.
---

# Working inside a Grove workspace

You are one agent in a fleet. Somebody is watching twenty workspaces at once and
cannot attach to yours to find out how it is going. Report your task phase so
they can tell scoping from delivery at a glance.

## What becomes public

When a ticket is attached and issue-ops is enabled, Grove keeps one live sticky
comment on that ticket. It shows the workspace status, branch and commit, your
reported phase and note, current activity, todo checklist, and other attached
refs. Anyone who can read the ticket can read this comment.

Keep the note and active-task text short and human-readable; raw tool output,
another agent's message, or harness markup is published verbatim. The mirror
says where you are, not what you found: write a real ticket comment when you
confirm a cause, land a fix, or open a pull request.

If nothing about your work should be public, raise that with whoever created the
workspace; do not silently stop reporting. If you are instead DRIVING a fleet,
use `using-grove`. For installation configuration, use `configuring-grove`.

## Keep the todo list current

Create the list on your first turn, before your first edit. Give every meaningful
piece of work its own item: a discovery, a code change, a verification step, or a
delivery step. Do not turn individual commands or files into items unless they
are independently useful to the person reading the list.

Mark an item complete as soon as that piece of work is complete. Keep exactly one
item in progress; move to the next only after completing or deliberately
superseding the current one. This is not private scratch planning: Grove renders
the list as the public checklist on every attached ticket. An empty list says
there is no plan, and stale items say work remains when it does not.

## Name the workspace

If its title is a generated id or its description is empty, set both once you
understand the task. Somebody is reading this name to tell your workspace from
twenty others.

On the host, infer the current workspace and use:

```bash
grove edit --title "parser validation" --description "Validate malformed input"
```

Over MCP, call `grove_update_workspace` with the workspace id, `title`, and
`description`. Do this once, not on every phase change; neither action moves the
worktree, branch, or tmux session. Inside a container, ask the orchestrator to
name it if neither command nor MCP is reachable.

## Write the file

Grove names the file for you, in your environment.

```
$GROVE_PHASE_FILE          an absolute path, already yours alone
```

If that variable is unset, fall back to `.grove/phase.json` at the top of your
worktree, not relative to wherever you happen to be standing.

```json
{"phase": "implementing", "note": "wiring the JSON parser"}
```

That is the whole contract. Overwrite the file each time. `phase` is one of the
six names below. `note` is optional, one line, under 200 characters, and it
should say what you are doing right now rather than restate the phase.

Never pick your own path. A worktree can host more than one agent, because
several workspaces run in a repo root and several agents run in one container, so
Grove gives each of you a distinct file. Writing anywhere else either overwrites
somebody else's report or lands where nothing reads it.

Write no timestamp. Grove takes the time from the file itself, so a field you add
is ignored and a field you got wrong would mislead. Extra keys are tolerated and
dropped.

**Write the file even when a shortcut exists.** A Grove workspace often runs
inside a container, where the daemon is unreachable and the `grove` command is
not installed. The worktree is mounted through, so the file lands on the host the
instant you write it. It is the one channel that works on every runtime and under
every harness.

On the **host**, two shortcuts do the same thing and are fine to use:

```bash
grove phase implementing --note "wiring the JSON parser"
```

or the `grove_set_workspace_phase` MCP tool. Both end up in the same file. Reach
for whichever is already in your hand — the point is that the report happens, not
which door it came through. Inside a container neither is available, so the file
is the answer.

## Attach the work item

Attach every issue you work and the pull request you open: attaching is what makes
Grove seed that ticket's phase entry and publish its sticky comment. On the host:

```bash
grove tickets attach 42
grove tickets attach https://example.com/acme/widgets/pull/17
```

Or call `grove_attach_ticket` with the workspace id and ref. A bare number,
`#42`, a full issue/PR URL, or `owner/repo#42` works; Grove infers the provider
and issue-versus-PR. If a bare number is ambiguous across enabled trackers, use a
URL or `owner/repo#42`. Attaching again is safe. Inside a container, ask the
orchestrator to attach it.

A workspace can carry related issues and the PR that closes them. Each ticket has
its own phase. Grove seeds the entry at `scoping`, keyed `"<provider>:<id>"`;
edit the existing key, never compose one.

```json
{
  "phase": "implementing",
  "tickets": {
    "gitea:498": {"phase": "verifying", "note": "gates green"},
    "gitea:499": {"phase": "planning", "note": "needs 498 merged"}
  }
}
```

- **The top-level `phase` is your own claim about the workspace as a whole**,
  reported exactly as described above — it never averages or rolls up what is
  underneath it.
- **Each ticket's `phase` is that ticket's own claim**, independent of the
  workspace's and of every other ticket's. If you are verifying issue A while
  issue B is untouched, that is `verifying` on `498` and `scoping` still on `499`
  — not one shared answer for both.
- **Overwrite the whole document each time, `tickets` included.** Changing one
  entry means writing every key, changed or not — the same rule as the top level.
- **Finishing one ticket moves its own entry to `done`, not the workspace's.**
  Report the workspace `done` only once nothing is left on any of them.
- **Attach a ticket the moment you learn it belongs to you.** Nothing is seeded,
  and nothing published, for a ref that is not attached yet.

## The six phases

Pick the one that matches the moment you are in. They are ordered and they
converge on `done`.

| Phase | You are here when |
|---|---|
| `scoping` | Reading the ticket, the code and the tests, working out what the job actually is |
| `planning` | You understand the problem and are choosing an approach or writing it down |
| `implementing` | You are editing files |
| `verifying` | Running tests, linters or the build, and reading your own diff back |
| `delivering` | Committing, pushing, opening or updating the pull request, writing the handoff |
| `done` | Handed off. Nothing is left for you to do on this task |

## Report blocked work

Set `"blocked": true` beside a phase — the workspace's own, or one ticket's —
when there is no way for you to finish it and it is not done. Report the phase
you actually reached: `verifying` plus blocked says the change is written and you
cannot get it tested; `scoping` plus blocked says you cannot even read the
ticket. Say why in the note.

This is a different `blocked` from the one the agent-activity axis already
reports. That one means you are waiting on a human right now, and it clears the
moment they answer — Grove infers it from your transcript, you never set it.
This one is a claim about the work itself: given what you know, it cannot be
finished, and answering a question does not by itself unblock it. Report both
when both are true.

## Report at transitions, not on a timer

Write the file when what you are doing changes: `scoping` before reading,
`planning` when choosing an approach, `implementing` at the first edit,
`verifying` for gates and diff review, `delivering` for the commit or PR, and
`done` only when nothing remains. Do not freshen a note because time passed.

Going backwards is correct: if verification overturns the design, report
`planning` again and say why.

## If the write fails, keep working

Reporting is best effort and it is never the task. A failed write costs you one
badge on somebody's dashboard. Abandoning the work, retrying in a loop, or
surfacing the failure as a blocker costs the whole ticket.

So write the file, ignore the result, and carry on. If it failed, the next
transition will overwrite it anyway. A malformed file is handled the same way at
the far end. Grove drops the report and shows nothing rather than breaking.

You do not need to gitignore the file. Grove excludes it from the worktree
itself. Never commit it and never mention it in your diff or your PR body.

## Attach the pull request

Your pull request is another ticket ref; Grove never discovers it from the
branch. Attach its URL through the same command or MCP tool above as you deliver.
This lets the ticket comment link the work to the PR and recognize a merged PR as
landed. It is a local Grove link, not a substitute for the project's convention
for issue references in PR text.

Inside a container, tell the orchestrator the PR URL so they can attach it.

## Shortcuts, where you can reach them

On the host, `grove phase implementing --note "wiring the JSON parser"` infers
your workspace from the current directory. Add `--ticket gitea:498` or
`--blocked` when needed. Over MCP, use `grove_set_workspace_phase` with the same
options; `grove_get_workspace_phase` and `grove_get_workspace_todo` read them
back. In a container, fall back to the file.
