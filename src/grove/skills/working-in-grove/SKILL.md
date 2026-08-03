---
name: working-in-grove
description: Use when you are the coding agent running INSIDE a Grove workspace — a git worktree Grove created for one task, on the host or in a container. Triggers on: reporting or updating your task phase or progress, GROVE_PHASE_FILE or .grove/phase.json, `grove phase`, grove_set_workspace_phase, keeping an attached issue or PR up to date, being asked how far along you are, or noticing you are in a worktree with a phase file. Covers the six phase names and when to write each, that your phase, note, todo checklist and activity line are PUBLISHED onto every attached ticket as a live comment, keeping several attached tickets current at once, and attaching the pull request you opened so the work is seen to land.
---

# Working inside a Grove workspace

You are one agent in a fleet. Somebody is watching twenty workspaces at once and
cannot attach to yours to find out how it is going. Report your task phase and
they can see it at a glance.

This is not status reporting for its own sake. Your transcript shows that you are
busy. It never shows whether you are still working out what the job is or already
pushing the branch, and those are the two facts a person deciding where to spend
attention actually needs. Only you can tell them apart, so only you can report
it.

## What you report may be public

When your workspace is linked to a ticket and issue-ops is enabled, Grove keeps a
live comment on that ticket built from what you report: your **phase and its
note**, your **todo checklist**, and your **current activity line**. That comment
is on the tracker, where anyone with access to the issue reads it — reviewers,
teammates, and on a public repository, strangers.

Three things follow, and none of them cost you extra work.

- **Keep the phase note and your active-task text short and human-readable.**
  Pasting raw tool output, another agent's message, or internal harness markup
  into either one publishes it verbatim.
- **Keep your todo list honest as you go.** It renders as the checklist, so an
  item you finish without marking it makes the public status wrong.
- **Nothing you report is a substitute for a real comment.** The mirror says
  where you are; it never says what you found. When you confirm or correct a root
  cause, land a fix, or open a pull request, write that on the ticket yourself.

If nothing about your work should be public, that is a conversation to have with
whoever created the workspace — not a reason to stop reporting.

If you are instead DRIVING a fleet rather than working inside one, that is
`using-grove`. Configuring the installation is `configuring-grove`.

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

## Keeping several tickets current at once

A workspace often carries more than one ticket — a cluster of related issues, or
an issue plus the PR that closes it. **One report updates all of them.** Grove
publishes the same body to every attached ref, so you never report per ticket and
you never need to know how many are attached.

What that means in practice:

- **Your phase describes the WORKSPACE, not one ticket.** If you are verifying
  issue A while issue B is untouched, the honest report is still `verifying` —
  the checklist is what says where each ticket stands.
- **So the checklist carries the per-ticket detail.** Name the ticket in the todo
  item (`#473 — bound the payload`) and readers on every attached issue can see
  which parts are theirs. This is the only place per-ticket progress exists.
- **Attach a ticket the moment you learn it belongs to you**, not at the end.
  Nothing is published for a ref that is not attached, so a ticket attached late
  has no history on its own thread.
- **Finishing one ticket is a checklist edit, not a phase change.** Do not report
  `done` because one of five is finished; `done` means the workspace has nothing
  left.

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

There is no `blocked` and no `failed`. Grove already tracks those on a separate
axis, and an agent stuck on a question is still inside some phase. Report the
phase you are in and let Grove report the trouble.

## Report at transitions, not on a timer

Write the file when what you are doing changes. On a normal task that is five or
six writes from start to handoff, which is roughly one per phase.

Do not report progress inside a phase. Ten writes of `implementing` with a
freshening note cost you ten turns and tell the watcher nothing the first one did
not. If you catch yourself updating the note because time has passed rather than
because the work moved, stop.

A good rhythm for a typical ticket looks like this.

1. You are handed the task. Write `scoping` before you start reading.
2. You have read enough to know what to build. Write `planning`.
3. You open the first file to edit. Write `implementing`.
4. The change is in and you run the test suite. Write `verifying`.
5. Gates pass and you start the commit. Write `delivering`.
6. The PR is up and you have nothing left. Write `done`.

**Going backwards is a correct report.** If verifying shows the design was wrong,
write `planning` again and say why in the note. Grove never enforces forward
motion, and an honest reversal is worth far more to the watcher than a phase that
only ever climbs.

## If the write fails, keep working

Reporting is best effort and it is never the task. A failed write costs you one
badge on somebody's dashboard. Abandoning the work, retrying in a loop, or
surfacing the failure as a blocker costs the whole ticket.

So write the file, ignore the result, and carry on. If it failed, the next
transition will overwrite it anyway. A malformed file is handled the same way at
the far end. Grove drops the report and shows nothing rather than breaking.

You do not need to gitignore the file. Grove excludes it from the worktree
itself. Never commit it and never mention it in your diff or your PR body.

## Link the pull request you opened

Grove never works your pull request out from the branch name. It knows the issue
your branch references and nothing more, so a PR reaches your workspace only if
somebody attaches it. On the host, that somebody is you, in one call as you
deliver.

```bash
grove tickets attach https://example.com/acme/widgets/pull/17
```

You never name the tracker. Grove infers the provider and whether the ref is an
issue or a pull request from the shape of what you pass, and it accepts a full
URL, `#42`, `42`, or `owner/repo#42`. It works out which workspace you are in
from the current directory. Attaching twice is a no op rather than a duplicate.

This is worth the one call because of what it gives the person watching. The
attached PR is how they learn your work landed, since Grove reads a merged pull
request as `merged` where the issues endpoint would only say closed. Without the
link they have your branch name and a guess.

Attaching is a local link and it fetches nothing. It is also not a substitute for
however your project wants issues referenced in PR text, which is a separate
question with its own rules. Follow the repo's convention there.

If you are inside a container you have no `grove` command, so skip this and say
in your handoff that the PR is open. Whoever is orchestrating attaches it from
outside.

## Shortcuts, where you can reach them

Two conveniences do the same job as the file when your workspace runs on the
host.

```bash
grove phase implementing --note "wiring the JSON parser"
```

The command works out which workspace you are in from the current directory, so
it takes no id. Over MCP the equivalents are `grove_set_workspace_phase` and
`grove_get_workspace_phase`, and `grove_get_workspace_todo` reads back the
checklist Grove already tracks for you.

Reach for these when they are there. Fall back to the file the moment either one
is missing, and treat that as ordinary rather than as a problem to report. A
containerized workspace has neither by design, and Grove leaves its MCP server
out of a container's config on purpose rather than register a binary that is not
installed.
