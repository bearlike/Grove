# Ticket providers

## Work arrives from the tracker

Grove ties each workspace to the ticket it serves. A branch is the work,
the ticket is why it exists, and the branch name is the source of truth.

<figure class="ms-shot">
  <div class="ms-shot__frame">
    <img src="../img/screenshots/issue-ops-sticky-comment.png" alt="Grove's status comment on a tracker issue. A table lists the phase, the checklist, the branch, the latest commit, a workspace link and the update time. Below it a six step diagram runs from Scoping to Done, then collapsed sections for latest activity, checklist and tracking.">
  </div>
  <figcaption class="ms-shot__body">One comment per ticket, rewritten in place. You read the phase, checklist and latest commit from the tracker itself.</figcaption>
</figure>

A ticket is an issue or a pull request, both in one list on a workspace,
because Gitea and GitHub number them in one space.

**Gitea Issues**, **GitHub Issues** and **Linear** are supported, all off
by default. See [configure ticket providers](configure-ticket-providers.md).

## What the link buys you

<div class="ms-grid ms-grid--3">
  <div class="ms-card">
    <span class="ms-card__title">Tickets on the row</span>
    <p class="ms-card__body">Compact pills on every workspace row: <code>ENG-123</code>, <code>GH#42</code>, <code>GTEA#5</code>, with the resolving pull request behind an arrow.</p>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">Detail on demand</span>
    <p class="ms-card__body">The detail view fetches title, status, assignee and URL on request. No background polling.</p>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">Start from a ticket</span>
    <p class="ms-card__body">Create from a ticket and Grove names the branch for you.</p>
  </div>
</div>

## Working the ticket

Grove keeps one comment on every ticket a workspace is working, and
rewrites it in place rather than posting a feed.

- Carries the task phase, checklist, branch, latest commit and a link
  into Grove.
- Flushes at most once every few seconds, so a busy agent costs one edit,
  not a stream.
- Reaches every ticket named, so a reader on either the issue or the
  pull request finds the other.
- Stops and says so when the workspace ends, keeping the last phase and
  a link to the session transcript.

[Issue ops](issue-ops.md) covers the body in detail.

## Several tickets, one workspace

A workspace can serve more than one attached ticket, and each one gets its
own phase claim rather than sharing the workspace's single answer. The issue
a pull request closes can read `verifying` while the pull request itself
already reads `delivering`, and each ticket's own comment carries only its
own claim. See [task phase](features-status.md#the-third-axis-task-phase)
for how an agent reports one, and `--ticket`/`--blocked` on
[`grove phase`](use-cli.md#grove-phase) for setting one from outside the
workspace.

The ticket list on a workspace ranks by the same rule everywhere it renders,
so the TUI, the web dashboard and the sticky comment never disagree about
what belongs at the top. Pull requests rank above issues. Within each, open
ranks above draft, above anything settled. Within that, the ticket furthest
along its own phase ranks first, then one marked `done`, then one nobody has
reported against yet. A ticket marked `done` ranks below one still in
progress even if it was the one updated moments ago, because the rank tracks
progress, not recency.

## The assignee is the work queue

Grove uses the tracker's own assignee field in both directions, off by
default.

- **Outbound.** Grove assigns its bot account to tickets its workspaces
  hold, so the fleet's work is findable with the tracker's own filters.
- **Inbound.** The daemon polls for open issues assigned to that account
  with no workspace behind them, and starts one holding the title, body
  and comment thread. Needs no CI runner and no webhook.

Three bounds apply. A handover is durable, so Grove can never pick the
same ticket up twice. At most `pickup_max_active` pickup workspaces run
at once and the next ticket waits a tick rather than being dropped. A
provider error backs that provider off instead of retrying into a rate
limit. Grove never unassigns the bot when work finishes, since the
assignment is the record of who did it.

```bash
grove tickets handover '#42'
grove tickets owned
grove tickets handback '#42'
```

## Deriving refs from the branch name

On create, and again on adopting an existing branch, Grove parses the
branch through every enabled provider. Each match becomes a `ticket_ref`
on the workspace.

A ref is **stored bare** — the provider, the id and whether it is an issue or
a pull request. A title, a status and an assignee are fetched on demand, and
never persisted, so a ticket renamed on the tracker is never shown stale.
Grove asks the tracker when a surface actually needs those details: the
terminal UI when you select the workspace, the web workspace page when it
opens, and issue-ops when it refreshes a ticket's comment. **Nothing polls a
tracker in the background**, so an unreachable tracker costs you titles and
nothing else.

| Provider | Recognizes | Example branch | Derived ref |
|---|---|---|---|
| **Linear** | Team key plus number (`ENG-123`), anywhere in the name. | `grove/ENG-123-short-description` | `ENG-123` |
| **GitHub** | A bare leading number, built-in `gh-`, or your `branch_prefix`. | `42-short-description` or `gh-42-short-description` | `GH#42` |
| **Gitea** | A bare leading number, built-in `gitea-`/`gtea-`, or your `branch_prefix`. | `5-short-description` or `gtea-5-short-description` | `GTEA#5` |

The default `worktree.branch_prefix` is `grove/`, so a Linear branch Grove
creates reads `grove/ENG-123-short-description`. Linear matches the key
wherever it sits, prefix or no prefix.

### The bare number is deliberately ambiguous

Gitea and GitHub share the same numeric grammar, so `123-fix` matches
both when both are enabled. Grove stores both refs, marks them
**ambiguous**, and flags the workspace until you attach the right one
and detach the wrong one. A keyed form is never ambiguous by
construction. `ENG-123` is Linear, `gh-42` is GitHub, `gtea-5` is Gitea.

## Creating a workspace from a ticket

Pass an optional `ticket: {provider, id}` at create time and Grove fetches
the title and builds a branch name around it.

```
{worktree.branch_prefix}{key}-{slug(title)}
```

Linear `ENG-123` titled `Short description` yields
`grove/ENG-123-short-description`. Parsing that branch later returns the
same ref, since the key is in the name.

## Manual attach and detach

Derived refs are a starting point, not a verdict. Attach a ticket Grove
did not infer, or detach one it did, to resolve an ambiguous match, link
a branch that predates the ticket, or link a pull request your agent
just opened. Only the stored refs change, never the branch name.

Attaching takes whatever you have open in your browser, and Grove infers
the provider and kind from the shape.

- A URL such as `https://github.com/acme/api/pull/301`, or the same shape
  on your Gitea host.
- A bare `#42` or `42`, resolved against the repo's enabled providers,
  same rule as [branch parsing](#the-bare-number-is-deliberately-ambiguous).
- A qualified `owner/repo#42`, for when a bare number would be ambiguous.
- A Linear key such as `ENG-123`.

Attaching is idempotent, so a re-attach to correct a wrong guess fixes it
in place.

```bash
grove tickets attach https://github.com/acme/api/pull/301
grove tickets detach '#42'
```

The MCP tools `grove_attach_ticket` and `grove_detach_ticket` take the
same `ref`. See [the CLI reference](use-cli.md#grove-tickets).

## Pull requests

A pull request is a ticket reference like an issue, carrying `kind:
"pull_request"` instead of `kind: "issue"`. Same registry, same attach
and detach flow, same status comment.

- **Merged is not closed.** A merged pull request reports `status:
  "merged"`, distinct from `"closed"`, because they mean different
  things to anyone deciding whether the work landed. Grove fetches the
  pulls endpoint specifically to tell them apart.
- Several issues typically resolve to one pull request, rendered as an
  arrow, issues first, colored by its state.

## API surface

The daemon exposes the ticket layer over HTTP.

| Method and path | Purpose |
|---|---|
| `GET /tickets/providers` | List enabled providers and config (no secrets). |
| `GET /tickets/assigned` | Tickets assigned to you across enabled providers. |
| `GET /tickets/{provider}/{id}` | Fetch one ticket. Reports `kind` and merged state for a pull request. |
| `POST /workspaces/{id}/tickets` | Attach a resolved `{provider, id}` or a raw `{ref}`. |
| `DELETE /workspaces/{id}/tickets/{provider}/{id}` | Detach by resolved provider and id. |
| `DELETE /workspaces/{id}/tickets?ref=...` | Detach by the same raw ref shapes. |

Workspace list and detail responses both carry `ticket_refs`, so a client
renders the pills without a second round trip.

## What Grove still never does

Grove writes comments, and assignees once you turn that on. Nothing else
on your tracker moves.

- **No status transitions.** Starting or stopping a workspace never moves
  a ticket to In Progress or Done. That stays yours.
- **No webhooks.** Grove polls, or fetches on a detail view. Nothing
  listens for provider events.

## See also

- [Configure ticket providers](configure-ticket-providers.md), field by field.
- [Issue ops](issue-ops.md), the status comment and driving a workspace from one.
- [CLI reference](use-cli.md#grove-tickets) and [MCP tools](use-mcp.md#tools).
- [Workspace lifecycle](features-workspace-lifecycle.md).
