# Ticket providers

Grove ties each workspace to the ticket it serves. A branch is the work.
The ticket is why the work exists. Grove reads the link straight off the
branch name, so the connection follows the branch wherever it goes. The
phrase to remember is that the branch name is the source of truth.

<figure class="ms-shot">
  <div class="ms-shot__frame">
    <img src="../img/screenshots/issue-ops-sticky-comment.png" alt="Grove's status comment on a tracker issue. A table lists the phase, the checklist, the branch, the latest commit, a workspace link and the update time. Below it a six step diagram runs from Scoping to Done, then collapsed sections for latest activity, checklist and tracking.">
  </div>
  <figcaption class="ms-shot__body">One comment per ticket, rewritten in place while the work runs. You read the phase, the checklist and the latest commit from the tracker itself.</figcaption>
</figure>

A ticket is an issue or a pull request. Both live in one list on a
workspace, because Gitea and GitHub number them in one space and thread
pull request comments through the issue endpoint.

**Gitea Issues**, **GitHub Issues** and **Linear** are supported, all off by
default. See [configure ticket providers](configure-ticket-providers.md) for
the setup.

## What the link buys you

<div class="ms-grid ms-grid--3">
  <div class="ms-card">
    <span class="ms-card__title">Tickets on the row</span>
    <p class="ms-card__body">Compact pills on every workspace row: <code>ENG-123</code>, <code>GH#42</code>, <code>GTEA#5</code>, with the pull request that resolves them following an arrow, colored by its own state. You read what a workspace is for without opening it.</p>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">Detail on demand</span>
    <p class="ms-card__body">The detail view fetches title, status, assignee and URL on request. No background polling.</p>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">Start from a ticket</span>
    <p class="ms-card__body">Create from a ticket and Grove names the branch for you, key and title slug baked in.</p>
  </div>
</div>

## Working the ticket

Grove keeps one comment on every ticket a workspace is working, and
rewrites it in place. The comment carries the agent's task phase, its
checklist, the branch and the latest commit, and a link back into Grove.
You learn where the work stands without opening anything.

One comment, never a feed. Updates fold together and flush at most once
every few seconds, so a busy agent costs the tracker a single edit instead
of a stream of them.

The same body reaches every ticket the workspace names. That is what lets a
reader on the issue find the pull request, and a reader on the pull request
find the issue.

When the workspace ends the comment says so and stops. It keeps the last
phase and points at the session transcript, because the workspace it used
to link is gone. [Issue ops](issue-ops.md) covers the body in detail.

## The assignee is the work queue

Trackers already have a field for who is working something. Grove uses it in
both directions, and both halves stay off until you turn them on.

Outbound, Grove assigns its bot account to the tickets its workspaces hold,
so your fleet's work is findable with the tracker's own filters. Inbound,
the daemon polls for open issues assigned to that account with no workspace
behind them and starts one. You assign the bot and a workspace appears,
holding the title, the body and the whole comment thread.

This needs no CI runner and no webhook, which matters on a deployment where
the comment command path cannot run at all.

Three bounds before you enable it. A ticket is handed over once and the
claim is durable, so Grove assigning the bot can never make Grove pick the
same ticket up again. At most `pickup_max_active` pickup workspaces run at
once and the next ticket waits a tick rather than being dropped. A provider
error backs that provider off instead of retrying into a rate limit.

```bash
grove tickets handover '#42'
grove tickets owned
grove tickets handback '#42'
```

Grove never unassigns the bot when the work finishes. The assignment is the
record of who did it.

## Deriving refs from the branch name

On create, and again when you adopt an existing branch, Grove parses the
branch through every enabled provider. Each match becomes a `ticket_ref`
on the workspace. Nothing is fetched to do this. The parse is pure string
work against the branch name. A network call happens later, when you open
a ticket's detail.

Each provider recognizes its own grammar in the branch name.

| Provider | Recognizes | Example branch | Derived ref |
|---|---|---|---|
| **Linear** | The team key plus number (`ENG-123`) anywhere in the name. | `grove/ENG-123-short-description` | `ENG-123` |
| **GitHub** | A bare leading number, the built-in `gh-` keyword, or your configured `branch_prefix`. | `42-short-description` or `gh-42-short-description` | `GH#42` |
| **Gitea** | A bare leading number, the built-in `gitea-` / `gtea-` keywords, or your configured `branch_prefix`. | `5-short-description` or `gtea-5-short-description` | `GTEA#5` |

The default `worktree.branch_prefix` is `grove/`, so a Linear branch
created by Grove reads `grove/ENG-123-short-description`. Linear matches
the `ENG-123` key wherever it sits in the name, prefix or no prefix.

### The bare number is deliberately ambiguous

Gitea and GitHub share the same numeric grammar, so `123-fix` is a valid
reference for both. With both enabled Grove cannot know which you meant. It
stores both refs and marks them **ambiguous**, the UI flags the workspace,
and you attach the right one and detach the wrong one.

A keyed form is never ambiguous. `ENG-123` is Linear, `gh-42` is GitHub and
`gtea-5` is Gitea, each by construction.

## Creating a workspace from a ticket

Pass an optional `ticket: {provider, id}` at create time. Grove fetches the
ticket title and builds a branch name around it.

```
{worktree.branch_prefix}{key}-{slug(title)}
```

Linear `ENG-123` titled `Short description` yields
`grove/ENG-123-short-description`. Parse that branch later and the same ref
falls back out, because the key is in the name. The branch carries its own
provenance.

## Manual attach and detach

Derived refs are a starting point, not a verdict. Attach a ticket Grove did
not infer, or detach one it did. That is how you resolve an ambiguous match,
link a workspace whose branch predates the ticket, and link the pull request
your agent just opened. The branch name stays as it is. Only the stored refs
change.

Attaching takes whatever you already have open in your browser.

- A URL such as `https://github.com/acme/api/pull/301`, or the same shape
  on your Gitea host.
- A bare `#42` or `42`.
- A qualified `owner/repo#42`, for when a bare number would be ambiguous.
- A Linear key such as `ENG-123`.

Grove infers the provider and the kind from the shape. A bare number uses
the repo's *enabled* providers, the same rule as [branch
parsing](#the-bare-number-is-deliberately-ambiguous). Attaching is
idempotent, and re-attaching to correct a wrong guess fixes it in place.

```bash
grove tickets attach https://github.com/acme/api/pull/301
grove tickets detach '#42'
```

The MCP tools `grove_attach_ticket` and `grove_detach_ticket` and the
daemon's `POST /workspaces/{id}/tickets` take the identical free text
`ref`. See [the CLI reference](use-cli.md#grove-tickets) and [MCP
tools](use-mcp.md#tools).

## Pull requests

A pull request is a ticket reference like an issue, carrying `kind:
"pull_request"` instead of `kind: "issue"`. That single field is the whole
difference. Same provider registry, same attach and detach flow, same
status comment.

**Merged is not closed.** A merged pull request reports `status: "merged"`,
distinct from `"closed"`. The two mean different things to anyone deciding
whether the work landed, so Grove fetches the tracker's pulls endpoint
specifically to tell them apart.

Several issues typically resolve to one pull request. The TUI and web
dashboard render that as an arrow, issues first and the pull request last,
colored by its state.

## API surface

For the web dashboard and MCP clients, the daemon exposes the ticket layer
over HTTP.

| Method and path | Purpose |
|---|---|
| `GET /tickets/providers` | List enabled providers and their config (no secrets). |
| `GET /tickets/assigned` | Tickets assigned to you across enabled providers. |
| `GET /tickets/{provider}/{id}` | Fetch one ticket. Reports `kind` and the merged state when the id names a pull request. |
| `POST /workspaces/{id}/tickets` | Attach a resolved `{provider, id}` or a raw `{ref}`. |
| `DELETE /workspaces/{id}/tickets/{provider}/{id}` | Detach by resolved provider and id. |
| `DELETE /workspaces/{id}/tickets?ref=...` | Detach by the same raw ref shapes. |

Workspace list and detail responses both carry `ticket_refs`, so a client
renders the pills without a second round trip.

## What Grove still never does

Grove writes comments, and assignees once you turn that on. Nothing else on
your tracker moves.

- **No status transitions.** Starting or stopping a workspace never moves a
  ticket to In Progress or Done. That stays yours.
- **No webhooks.** Grove polls, or fetches when you open a detail view.
  Nothing listens for provider events.

Your tracker stays the source of truth. Grove reports what its own
workspaces are doing and leaves every other field alone.

## See also

- [Configure ticket providers](configure-ticket-providers.md): the `tickets` config section, field by field.
- [Issue ops](issue-ops.md): the status comment, and driving a workspace from a ticket comment.
- [CLI reference](use-cli.md#grove-tickets) and [MCP tools](use-mcp.md#tools): the `grove tickets` verbs and their MCP equivalents.
- [Workspace lifecycle](features-workspace-lifecycle.md): create, adopt, pause, and kill.
