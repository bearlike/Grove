# Web dashboard

Grove is a terminal program first. A web dashboard ships alongside it for async access to your whole
fleet from any device on your network, a laptop in the next room, a second monitor, or a phone away from
your desk. You glance at every workspace, drop into one to read the agent's transcript, send it a
follow-up, and create or tear down a workspace, all without a terminal. Think of it as a web IDE for your
agents: the same engine the TUI drives and the same workspaces an agent can spawn over
[MCP](use-mcp.md), reachable from a browser.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-home-grid.png" alt="Grove web dashboard: a composer hero at the top for starting a workspace, the repo-grouped workspace cards below, a scope rail on the left, and the daemon status bar along the bottom" /></div>
  <figcaption class="ms-shot__body">The unified surface. A composer at the top to start a workspace, every workspace below it grouped by repository, a scope rail on the left, and the daemon status bar along the bottom.</figcaption>
</figure>

## One unified surface

The home page is one surface for the whole fleet. A composer holds the center as the hero: type a task,
press Enter, and Grove creates a workspace and drops you into it (more on the composer below). Below it,
every workspace the daemon knows about renders once, grouped by repository. The cards use the same status
glyphs and colors as the TUI, so the two surfaces read like siblings, not strangers.

Each card carries the workspace title, its placement and status badge, the branch and its base, the
agent, a one-line "happening now" summary, the ahead, behind, and dirty counts, and the last commit with
a relative time. Tap a card to open that workspace.

A collapsible rail runs down the left as navigation and scope, not a second copy of the grid. It holds a
search box, a repository scope switcher (an **All workspaces** row, then one row per repo with a count),
the agent-state filters, and an attention-only toggle. Scope into a repository and the grid narrows to
it, and the composer's create target follows along, so what you are looking at and what you would create
in stay in agreement. Collapse the rail with the header toggle or `[` when you want the full width for
cards.

The grid rides a live event stream from the daemon, so a fresh commit, a new dirty file, or a workspace
changing state shows up within a second or two, with no manual refresh. A status bar along the bottom
reports the daemon's health, its version, its uptime, and the workspace count. (There used to be a
separate `/activity` wall. It folded into this one surface, so `/activity` now just redirects here and
old bookmarks still land.)

## Watching a live agent

A card whose agent is working carries a **Live** toggle. Flip it and a single focused pane opens inline
above the grid, mirroring that agent's real terminal, colors and box-drawing intact, streamed over SSE.
Only one pane is live at a time, so the surface stays cheap on a phone. What the tiles and signals mean
is on the [agent activity and sessions](features-activity.md) page.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-focused-pane.png" alt="The unified surface with a working workspace's Live toggle flipped, a single focused terminal pane mirroring the agent's tmux output above the grid" /></div>
  <figcaption class="ms-shot__body">A live focused pane. Flip a working card's Live toggle and its terminal mirrors inline above the grid, one pane at a time.</figcaption>
</figure>

## The workspace IDE shell

Open a workspace and you land on its detail page, a transcript-first IDE shell. A compact context bar
runs across the top: a back arrow, the workspace title, its placement, the live agent or lifecycle state
badge, and a branch-summary popover that folds the ahead, behind, and dirty counts plus recent commits
into one click. The lifecycle action buttons live in that same bar (more on those below).

Under the context bar sits the agent surface, with two tabs.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-workspace-detail.png" alt="The workspace detail page: a context bar with title, state badge, and lifecycle buttons over the Transcript tab showing the agent conversation and a steer composer" /></div>
  <figcaption class="ms-shot__body">The workspace IDE shell. The context bar carries identity, the live state, and the lifecycle buttons; the Transcript tab holds the conversation and the steer composer.</figcaption>
</figure>

The **Transcript** tab is a real chat panel. It shows the conversation as it unfolds: your messages and
the agent's replies, tool calls grouped into collapsible runs, and background notifications. A composer
sits at the bottom. Type into it ("Steer the agent...") and your message goes straight to the running
agent as a follow-up, the same as typing into the terminal, just from the browser. While the agent is
working, an Interrupt button appears next to the composer so you can stop it mid-turn.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-workspace-terminal.png" alt="The workspace detail page on its Terminal tab, mirroring the agent's live tmux pane with a live-capture badge" /></div>
  <figcaption class="ms-shot__body">The Terminal tab. The agent's tmux pane mirrored in near real time, colors and box-drawing intact, with a live-capture badge.</figcaption>
</figure>

The **Terminal** tab mirrors the agent's live tmux pane, with real colors and box-drawing intact. It
refreshes itself every couple of seconds and carries a live-capture badge, so the pane tracks the
terminal in near real time without you reaching for a keyboard. On a wide screen you can switch from
tabs to a resizable split and watch the transcript and the terminal side by side; drag the handle to set
the ratio and it sticks.

## Creating a workspace with the composer

The composer at the top of the home surface is the single way to start a workspace. There is no separate
dialog. The first line of your prompt becomes the workspace title, and the full text becomes the agent's
first task as the session boots. Type a task and press Enter, and you land in the new workspace.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-composer.png" alt="The composer with a task typed in, an Agent and Model picker on the toolbar, and the Advanced disclosure open to show the repository, branch source, base ref, and skip-init controls" /></div>
  <figcaption class="ms-shot__body">The composer. Type a task, pick the agent and model, and open Advanced for the repository, branch mode, and skip-init.</figcaption>
</figure>

The toolbar carries an **Agent** picker and, for agents that take one, a **Model** picker. A context-chip
row below names the repository, the base, and the branch mode at a glance, with an **Advanced**
disclosure that reveals the rest: how the branch is made (*auto*, where Grove names one from the title
and a timestamp; *new*, where you name it and pick a base; *existing*, to check out a local branch;
*remote*, to track a remote branch; or *root*, to work in the repo root with no worktree), the base ref,
and a *skip init* toggle. A fullscreen toggle opens a roomy Markdown editor with a live preview for a
longer brief. It speaks the same wire contract as the TUI's create modal, so what you build in the
browser is exactly what the engine builds.

**Lifecycle actions.** The context bar on a workspace detail page carries *Pause*, *Resume*, *Respawn*,
and *Kill* buttons. Which ones appear depends on the workspace's current status and placement, the same
matrix the TUI uses, so you never see an action that cannot run. Pause, resume, and respawn fire
straight away. Kill is the one destructive verb, so it opens a confirm dialog with a delete-branch
checkbox whose default follows where the branch came from. The engine is the real precondition gate, so
an action that cannot run surfaces a plain refusal rather than doing something surprising.

## Running it

The dashboard is two processes. The daemon exposes Grove's engine over HTTP. The web app is a Next.js
server that the browser talks to. Build the web app once, then run both.

```bash
# Build the web app once (repeat after each upgrade)
cd webapp && npm install && npm run build

# Run the two processes
grove daemon serve     # terminal 1, loopback, port 7421
npm run start          # terminal 2 from webapp/, serves the build on 0.0.0.0:3000
```

Open <http://127.0.0.1:3000> on the same machine. The web app binds `0.0.0.0`, so any phone or laptop
on the same network reaches it at `http://<machine-ip>:3000`. If the daemon runs on a different host or
port, point the web app at it with `GROVE_DAEMON_URL` in `webapp/.env.local`. The first time a new
device connects, it has to [pair](use-auth.md) with the host.

Editing the dashboard's own code is a separate, contributor task. The development server, the wire-type
codegen, and the test suites live in the
[`webapp/` README](https://github.com/bearlike/Grove/tree/current/webapp), not here.

## How it stays loopback-only

```
Browser (LAN:3000)  ──http──▶  Next.js (0.0.0.0:3000)  ──http──▶  Daemon (127.0.0.1:7421)
                                 │ /api/grove/*  (BFF proxy)
```

The browser only ever calls the web app's own origin at `/api/grove/*`. That keeps everything
same-origin, so there is no CORS to configure. The Next.js server works as a backend-for-frontend. It
proxies those calls to the daemon at `GROVE_DAEMON_URL`, which defaults to `http://127.0.0.1:7421`. It
also attaches the paired session's token on the server side, so the daemon's token never reaches the
browser.

Picture the web app as a receptionist at a front desk. Visitors talk to the receptionist. The
receptionist carries each request to the back office and brings the answer out. The daemon stays in
that back office, bound to loopback, and never meets the public directly. That separation is the whole
security story, and [authentication & pairing](use-auth.md) covers the rest.

## Always-on with systemd

To keep both processes running across reboots on a Linux host, build the web app once and render the
user units with the web app opted in.

```bash
make webapp-build                 # npm ci + npm run build
WITH_WEBAPP=1 make systemd        # write grove-daemon + grove-webapp units
WITH_WEBAPP=1 make systemd-enable # reload, enable, start now
loginctl enable-linger "$USER"    # survive logout (remote hosts)
```

The web app unit `Wants` the daemon rather than `Requires` it. So a daemon hiccup leaves the dashboard
up to report "unreachable", instead of taking it down too. The service runs the production build and
does not rebuild itself. After you pull new source, run
`make webapp-build && systemctl --user restart grove-webapp`. You can override the ports with
`DAEMON_PORT=7777 WEBAPP_PORT=3030 WITH_WEBAPP=1 make systemd`.

## Reaching it from outside the network

The daemon's loopback bind is deliberate, so there is no blessed way to expose it directly. To reach a
remote host's dashboard, forward the web app's port over SSH.

```bash
ssh -N -L 3000:127.0.0.1:3000 you@remote-host
```

Then open <http://127.0.0.1:3000> on your own machine. You can also put a real tunnel in front of the
web app, such as Tailscale, WireGuard, or an authenticated reverse proxy. Widening the daemon's bind is
the wrong lever, because pairing assumes loopback. See the [security model](use-auth.md#the-security-model).

## See also

- [Authentication & pairing](use-auth.md): how a new device gets access.
- [CLI](use-cli.md): the `grove daemon serve` options and the `grove auth` group.
- [Agent activity and sessions](features-activity.md): the signals behind the live grid and the focused pane.
- [Status semantics](features-status.md): what each glyph and color means.
- Building or hacking on the dashboard? That is the [`webapp/` README](https://github.com/bearlike/Grove/tree/current/webapp), the contributor surface, kept separate from this product guide.
