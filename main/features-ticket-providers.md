# Ticket providers

Grove can tie each workspace to the ticket it serves. A branch is the
work; the ticket is why the work exists. Grove reads the link straight
off the branch name, so the connection follows the branch wherever it
goes. The phrase to remember: the branch name is the source of truth.

Three providers are supported: **Gitea Issues**, **GitHub Issues**, and
**Linear**. All three are off by default. Turn on the ones your team uses
in config. See [configure ticket providers](configure-ticket-providers.md)
for the setup.

## What the link buys you

<div class="ms-grid ms-grid--3">
  <div class="ms-card">
    <span class="ms-card__title">Tickets on the row</span>
    <p class="ms-card__body">The TUI shows compact pills on each workspace row: <code>ENG-123</code>, <code>GH#42</code>, <code>GTEA#5</code>. You read what a workspace is for without opening it.</p>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">Detail on demand</span>
    <p class="ms-card__body">The detail view fetches the ticket title, status, assignee, and URL on request. No background polling, no webhooks.</p>
  </div>
  <div class="ms-card">
    <span class="ms-card__title">Start from a ticket</span>
    <p class="ms-card__body">Create a workspace from a ticket and Grove names the branch for you, ticket key and a slug of the title baked in.</p>
  </div>
</div>

## Deriving refs from the branch name

On create, and again when you adopt an existing branch, Grove parses the
branch through every enabled provider. Each match becomes a `ticket_ref`
on the workspace. Nothing is fetched to do this; the parse is pure string
work against the branch name. A network call only happens later, when you
open a ticket's detail.

Each provider recognizes its own grammar in the branch name:

| Provider | Recognizes | Example branch | Derived ref |
|---|---|---|---|
| **Linear** | The team key plus number (`ENG-123`) anywhere in the name. | `grove/ENG-123-short-description` | `ENG-123` |
| **GitHub** | A bare leading number, the built-in `gh-` keyword, or your configured `branch_prefix`. | `42-short-description` or `gh-42-short-description` | `GH#42` |
| **Gitea** | A bare leading number, the built-in `gitea-` / `gtea-` keywords, or your configured `branch_prefix`. | `5-short-description` or `gtea-5-short-description` | `GTEA#5` |

The default `worktree.branch_prefix` is `grove/`, so a Linear branch
created by Grove reads `grove/ENG-123-short-description`. Linear matches
the `ENG-123` key wherever it sits in the name, prefix or no prefix.

### The bare number is deliberately ambiguous

Gitea and GitHub share the same numeric grammar. A bare number like
`123-fix` is a valid issue reference for both. When both providers are
enabled, that branch matches both, and Grove cannot know which one you
meant. So it stores both refs and marks them **ambiguous**. The UI flags
the workspace, and you resolve it by hand: attach the right one, detach
the wrong one.

A keyed form is never ambiguous. `ENG-123` is Linear, `gh-42` is GitHub,
`gtea-5` is Gitea, each by construction. If you run both numeric
providers, prefer a keyword (or set a distinct `branch_prefix` per
provider) and the parse stays clean.

!!! tip "When in doubt, key it"
    The fastest way to avoid an ambiguous ref is to name the branch with a
    provider keyword: `gh-42-...`, `gtea-5-...`, or the Linear key
    `ENG-123-...`. A bare number is convenient, but it commits Grove to a
    guess only you can make.

## Creating a workspace from a ticket

Pass an optional `ticket: {provider, id}` at create time. Grove fetches
the ticket title and builds a ticket-aware branch name:

```
{worktree.branch_prefix}{key}-{slug(title)}
```

So creating from Linear `ENG-123` titled "Short description" with the
default prefix yields `grove/ENG-123-short-description`. The next time
that branch is parsed, the same ref falls right back out, because the key
is in the name. The branch carries its own provenance.

## Manual attach and detach

Derived refs are a starting point, not a verdict. You can attach a ticket
Grove did not infer, or detach one it did. This is how you resolve an
ambiguous match, and how you link a workspace whose branch name predates
the ticket. The branch name stays as it is; only the stored refs change.

## API surface

For the web dashboard and MCP clients, the daemon exposes the ticket
layer over HTTP:

| Method and path | Purpose |
|---|---|
| `GET /tickets/providers` | List enabled providers and their config (no secrets). |
| `GET /tickets/assigned` | Tickets assigned to you across enabled providers. |
| `GET /tickets/{provider}/{id}` | Fetch one ticket: title, status, assignee, URL. |
| `POST /workspaces/{id}/tickets` | Attach a ticket ref to a workspace. |
| `DELETE /workspaces/{id}/tickets/{provider}/{id}` | Detach a ticket ref. |

Workspace list and detail responses both carry `ticket_refs`, so a client
renders the pills without a second round trip.

## What this is not (MVP scope)

The first release reads tickets; it does not drive them. Three things are
out of scope on purpose:

- **No bidirectional status sync.** Grove never writes ticket status back.
- **No auto state transitions.** Starting or stopping a workspace does not
  move the ticket to "In Progress" or "Done".
- **No webhooks.** Grove fetches on demand or when you open a detail view.
  Nothing listens for provider events.

Read-only by design keeps the integration safe to enable and cheap to
reason about. Your ticket tracker stays the source of truth for status;
Grove just shows you the part you need while you work.

## See also

- [Configure ticket providers](configure-ticket-providers.md): the `tickets` config section, field by field.
- [Branch provenance](features-branch-provenance.md): how Grove tracks who created a branch.
- [Workspace lifecycle](features-workspace-lifecycle.md): create, adopt, pause, and kill.
