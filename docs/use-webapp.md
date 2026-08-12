# Web dashboard

## Your fleet in a browser

Grove is a terminal program first. The web dashboard puts the same fleet on any device: read a transcript, steer an agent, create or tear down a workspace.

<video preload="auto" poster="../img/posters/web-still.png">
  <source src="../videos/2-grove-web.mp4" type="video/mp4" />
</video>

---

## The rail

A collapsible rail runs down the left of every route ([`app-sidebar.tsx`](repo:webapp/components/grove/shell/app-sidebar.tsx)): a **New workspace** button, a search box and a filter menu, then the fleet as one flat list sorted by recency — each row an agent mark, title, `project · branch`, and a single trailing glyph for whatever is most urgent about that row (an attention mark, or how far along its task phase is). Below the list sit the destinations that are not "a workspace" — **Usage** and **Sessions** — and the account menu, which carries your identity, the theme toggle, and sign out. ++bracket-left++ hides the rail, a phone opens it as a drawer.

The filter menu ([`fleet-filter.tsx`](repo:webapp/components/grove/fleet/fleet-filter.tsx)) is the one place narrowing happens, reused by both the rail and the fleet grid: **Needs attention**, agent state, and project, each with a live count. A flat list rather than one grouped by project is deliberate — a Grove workspace is often empty and often momentary, so a heading per repo mostly reads "0, no workspaces yet."

## The fleet

The landing route (`/`) is the fleet itself ([`fleet-dashboard.tsx`](repo:webapp/components/grove/fleet/fleet-dashboard.tsx)): the same search and filter as the rail, above a grid of workspace cards, newest activity first.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-home.png" alt="Grove's fleet dashboard: a flat, attention-sorted grid of workspace cards behind a session rail listing every workspace by recency" /></div>
  <figcaption class="ms-shot__body">The fleet, newest activity first.</figcaption>
</figure>

Each card ([`workspace-card.tsx`](repo:webapp/components/grove/fleet/workspace-card.tsx)) answers three questions in reading order: what this is (title, repo, branch), what is happening (agent state, task phase, runtime, an attention badge when it needs you), and how much it has done (lines changed, commits ahead and behind, todo progress, linked tickets). Open one for the full transcript and work panel.

## Creating a workspace

**New workspace**, in the rail or on the fleet page, opens a dialog ([`create-workspace-dialog.tsx`](repo:webapp/components/grove/fleet/create-workspace-dialog.tsx)). Only the project, title and agent are required — everything else falls through to the [configuration cascade](features-cascade.md), which already holds better defaults than a form would:

- **Task**, sent to the agent as its first message. Optional — you can also start it blank and type the first message once the workspace opens.
- **Agent**, and a **Model** for agents that expose a catalog.
- **Runtime**, *Host* or *Container*, defaulting to the project's own `container.enabled`.
- **Brief**, whether Grove sends its own briefing to the agent, defaulting to the project's setting.
- **Branch**, auto-named from the title, an existing local or remote branch, or a new name you choose.
- **Ticket**, an issue id on Gitea, GitHub or Linear to link and [publish progress to](features-ticket-providers.md).

## Working in a session

A workspace's detail page (`/w/[id]`) has three zones: rail, transcript, work panel.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-workspace.png" alt="A Grove workspace page: the left session rail, a centered agent transcript column with a steer composer at its foot, and a work panel on the right with Terminal, Changes, Files, Info and Controls tabs" /></div>
  <figcaption class="ms-shot__body">The three-zone workspace shell.</figcaption>
</figure>

The header ([`shell-header.tsx`](repo:webapp/components/grove/shell/shell-header.tsx)) carries the workspace title, the agent's live status, an **Interrupt** button while it is running, and the pane switcher. The work panel ([`work-panel.tsx`](repo:webapp/components/grove/workspace/work-panel.tsx)) has five tabs:

| Tab | What it shows |
|---|---|
| **Terminal** | The agent's live tmux pane, colors and box-drawing intact, over its own SSE stream. |
| **Changes** | Divergence from the base branch, working-tree churn, and the commit list since the fork point. |
| **Files** | The per-file diffs the agent itself reported. |
| **Info** | Task phase, linked tickets, activity metrics, identity, branch, timeline, and the lifecycle actions. |
| **Controls** | The model catalog with a switch, plus enumerated slash commands, skills, and configured MCP servers. Empty for an agent with none. |

A wide window's pane switcher splits transcript and panel side by side; narrower it opens single-pane, transcript first. Lifecycle — **Pause**, **Resume**, **Respawn**, **Kill** — lives on the Info tab, not the header: the engine's own gate is the real authority, and these buttons mirror it rather than assume it.

---

## Steering the agent

The transcript ([`thread.tsx`](repo:webapp/components/grove/workspace/thread.tsx)) shows your messages, the agent's replies, and tool calls grouped into collapsible runs. The composer stays enabled while the agent works: ++enter++ sends, ++shift+enter++ makes a new line, and the header's **Interrupt** stops it mid-turn — steering never waits for the composer's own send button to change into one.

A live question from the agent renders as a card riding in the thread's own footer, a sibling of the message stream rather than a message itself ([`pending-question.tsx`](repo:webapp/components/grove/workspace/pending-question.tsx)): a single choice sends on tap, a multi-select or free-text question shows a **Submit**. An agent asking to proceed with its plan has no answer shape on the wire, so it renders read-only — you approve it the same way you would in the terminal.

---

## The Session Catalog

**Sessions** in the rail lists every agent session Grove can find on the machine, searchable by title, location, agent kind or branch. Open one with a known location and the conversation renders read only, with no composer mounted. [Session history](features-activity.md#session-history) covers the same ground in the TUI and CLI.

---

## The usage audit

**Usage** in the rail answers what the fleet actually spent. It is derived
entirely from transcripts already on disk, so it needs no invoice from a
provider and works with the network off.

<div class="swiper ms-shots">
  <div class="swiper-wrapper">
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/webapp-usage.png" alt="Grove's usage audit: a coverage bar naming each indexed source, tiles for sessions, turns, tool calls, files changed, projects and accounts, a daily token heatmap, and a weekly mix chart split by agent">
        <figcaption>Coverage, the headline tiles, a year of daily tokens, and the subscription windows.</figcaption>
      </figure>
    </div>
    <div class="swiper-slide">
      <figure>
        <img loading="lazy" src="../img/screenshots/webapp-usage-detail.png" alt="The lower half of Grove's usage audit: a per-model breakdown with token counts and a table of the most recent sessions">
        <figcaption>Further down: what each model cost you, and the sessions behind the totals.</figcaption>
      </figure>
    </div>
  </div>
  <div class="swiper-pagination"></div>
  <div class="swiper-button-prev"></div>
  <div class="swiper-button-next"></div>
</div>

The tiles count sessions, turns, tool calls and their failures, files changed,
projects and accounts. A daily heatmap puts a year of activity on one grid,
breakdowns split any metric by provider, account, project, model, tool or token
class, and a session table lists the runs behind the totals. Where you hold a
subscription Grove can read, its windows appear beside the spend.

Two numbers describe a session's length and they are not interchangeable. Clock
time is the wall clock, the session's active intervals merged so concurrency
counts once. Compute time sums the same intervals across the main thread and
every sub-agent, so a session that ran six agents can bill more compute than it
lasted. The Info tab shows both, and splits compute into model wait and tool
time.

Cost appears only where a price table and per-class token counts both exist, and
says "not measured" otherwise rather than showing a fabricated zero. The same
rule governs subscription windows: a plan Grove cannot read reports that it
could not read it. See [telemetry and tracing](features-telemetry.md) for the
other half of this, where the same sessions become queryable traces.

---

## Running it

The dashboard is two processes: a daemon exposing Grove's engine over HTTP, a Next.js app the browser talks to.

```bash
# Build the web app once (repeat after each upgrade)
make webapp-build

# Run the two processes
grove daemon serve     # terminal 1, loopback, port 7421
cd webapp && npm run start   # terminal 2, serves the build on 0.0.0.0:3000
```

Open <http://127.0.0.1:3000> on the same machine, or `http://<machine-ip>:3000` from a phone. Point it at a remote daemon with `GROVE_DAEMON_URL` in `webapp/.env.local`. A new device must [pair](use-auth.md) first. Dev server and tests are in the [`webapp/` README](https://github.com/bearlike/Grove/tree/current/webapp).

---

## How it stays loopback-only

```mermaid
flowchart TB
    Browser(["Browser<br/>LAN:3000"])
    Web(["Next.js<br/>0.0.0.0:3000"])
    Daemon(["Daemon<br/>127.0.0.1:7421"])
    Browser -->|http| Web
    Web -->|"http, /api/grove/* (BFF proxy)"| Daemon
```

The browser only calls the web app's own origin at `/api/grove/*`, so everything stays same origin with no CORS to configure. The Next.js server proxies each call to the daemon, attaching the paired session's token server side. Picture the web app as a receptionist carrying each request to the back office. [Authentication & pairing](use-auth.md) covers the rest.

---

## Always-on with systemd

Build the web app, then render the user units with it opted in:

```bash
make webapp-build                 # npm ci + npm run build
WITH_WEBAPP=1 make systemd        # write grove-daemon + grove-webapp units
WITH_WEBAPP=1 make systemd-enable # reload, enable, start now
loginctl enable-linger "$USER"    # survive logout (remote hosts)
```

The web app unit `Wants` the daemon rather than `Requires` it, so a daemon hiccup reports "unreachable" without taking the dashboard down too. Run `make webapp-build && systemctl --user restart grove-webapp` after pulling new source. Ports override with `DAEMON_PORT=7777 WEBAPP_PORT=3030 WITH_WEBAPP=1 make systemd`.

---

## Reaching it from outside the network

The daemon's loopback bind is deliberate, so there is no blessed way to expose it. Forward the web app's port over SSH:

```bash
ssh -N -L 3000:127.0.0.1:3000 you@remote-host
```

Open <http://127.0.0.1:3000> on your own machine. A real tunnel (Tailscale, WireGuard, a reverse proxy) works too. Widening the daemon's bind is the wrong lever. See the [security model](use-auth.md#the-security-model).

---

## See also

- [Authentication & pairing](use-auth.md): how a new device gets access.
- [CLI](use-cli.md): `grove daemon serve` and `grove auth`.
- [Agent activity and sessions](features-activity.md): the signals behind the rail and terminal pane.
- [Status semantics](features-status.md): what each glyph and color means.
- [`webapp/` README](https://github.com/bearlike/Grove/tree/current/webapp): the contributor surface.
