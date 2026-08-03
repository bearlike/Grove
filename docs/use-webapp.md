# Web dashboard

Grove is a terminal program first. A web dashboard ships alongside it for async access to your whole
fleet from any device on your network: a laptop in the next room, a second monitor, or a phone away from
your desk. You glance at every session, drop into one to read the agent's transcript, steer it with a
follow-up, and create or tear down a workspace, all without a terminal. Think of it as an agent
development environment in the browser: the same engine the TUI drives and the same workspaces an agent
can spawn over [MCP](use-mcp.md), reachable from anywhere on your LAN.

The mental model is "work in a session, switch sessions in a rail," not "watch a wall of cards." Every
route shares one shell: a persistent left session rail (your thread list) and a slim header. Nothing
sits along the bottom.

<video autoplay muted loop playsinline preload="auto" poster="../img/posters/web-still.png" style="width:100%;max-width:960px;height:auto;display:block;margin:0 auto 1.75rem;">
  <source src="../videos/2-grove-web.mp4" type="video/mp4" />
</video>

---

## The landing surface

The landing route (`/`) opens on a centered composer as the default. Type a task, press Enter, and Grove
creates a workspace and drops you into it (more on the composer below). The fleet is not hidden while you
do this: it lives one glance away in the rail, plus a quiet "Recent" strip of the most-recently-active
workspaces below the composer.

A persisted `Hero | Overview` toggle sits above the composer. **Hero** (the default) is the composer view
just described. **Overview** is the rich workspace-card grid, every workspace the daemon knows about,
each card carrying the same status glyphs and colors as the TUI so the two surfaces read like siblings.
Your choice sticks across reloads. The toggle is a quiet segmented control, never the terracotta accent
(that is reserved for the composer's send button).

Each Overview card, in [`card.tsx`](repo:webapp/components/workspace/card.tsx), shows the agent-state
glyph, the title, and a relative time on its top line, a one-line "happening now" summary below, then two
aligned stat rows: provenance (branch, ahead, behind, dirty) and activity (turns, tool calls, tokens). A
card also carries the agent's reported [task phase](features-status.md#the-third-axis-task-phase) when it
has reported one, and its todo progress as a bounded count.
A working card carries a **Live** toggle. Flip it and a single focused pane mirrors that agent's real
terminal inline, streamed over SSE, colors and box-drawing intact. Only one pane is live at a time, so
the surface stays cheap on a phone.

The grid and the rail both ride a live event stream from the daemon, so a fresh commit, a new dirty
file, or a workspace changing state shows up within a second or two, with no manual refresh. (There used
to be a separate `/activity` wall. It folded into this one surface, so `/activity` now just redirects
here and old bookmarks still land.)

---

## The session rail

A collapsible rail runs down the left on every route. It is Grove's thread list, built in
[`session-rail.tsx`](repo:webapp/components/layout/session-rail.tsx): one flat, cross-project list of
sessions sorted by recency, latest first. There are no repository sections and no scope switcher. What
you are looking at is simply every session, most-recently-touched at the top.

Each row is two lines. The first carries a small state dot, the session title, and how long ago it was
created. The second is the provenance line, `project · branch · +N/−M`, so you can tell one session from
another at a glance without opening it. A session that needs your attention (waiting or blocked) signals
inline through its dot color and a faint row tint, rather than jumping into a separate group.

Above the list sit a search box and one compact filter dropdown
([`sidebar-filter.tsx`](repo:webapp/components/layout/sidebar-filter.tsx)). The filter is the single
organizing instrument: toggle which agent states show, flip "Needs attention only," check or uncheck
individual projects, and reveal unmapped sessions (history-only rows that are hidden by default). Every
one of those choices persists.

Below the list is the rail footer, in
[`workspace-sidebar.tsx`](repo:webapp/components/layout/workspace-sidebar.tsx). It carries the daemon's
health and live uptime, the version and any update nudge, the workspace count, and a user-and-host
identity row with a link to the project on GitHub. This is where the fleet's system telemetry lives now.

> [!NOTE] Where the old status bar went
> Earlier builds put daemon health, uptime, version, and the workspace count in a bar along the bottom of
> the screen. That bar is gone. Every reading it carried moved into the rail footer, so the telemetry is
> still one glance away and the bottom of every page is now free for content.

Collapse the rail with the header toggle or `[` and it hides entirely to give content the full width.
There is no half-collapsed icon strip. On a phone the rail opens as a left drawer from the hamburger.

---

## The Session Catalog

The rail shows sessions from the projects Grove already knows about. Click **Sessions** in the header
and you get every agent session on the machine instead, whether or not Grove ever heard of the repo it
ran in.

Rows group by project, newest session first within each group. Each carries the agent kind, its git
branch, how long ago it last changed, whether Grove launched it or you started it by hand, and a live
dot when a matching agent process is still running in that directory. A session whose repository Grove
never manages shows up here exactly the same as one it does; the point of this screen is that "Grove
does not know about it" is not the same thing as "it does not exist."

Open any row and its full conversation renders read-only: the same transcript view the session detail
page uses, minus the composer and every other control. There is nothing to steer here, only history to
read. A session whose transcript never recorded where it ran still lists, dimmed, with a note explaining
why it will not open.

This screen reads a metadata scan, not a full transcript parse, so it stays fast even with hundreds of
sessions on disk. See [Session history](features-activity.md#session-history) for how the same catalog
reaches the TUI and the CLI.

---

## Working in a session

Open a session and you land on its detail page (`/w/[id]`), a three-zone shell: the shared rail on the
left, the transcript in a centered reading column, and a tabbed work panel on the right.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-workspace.png" alt="A Grove session page: the left session rail, a centered agent transcript column with a steer composer at its foot, and a work panel on the right with Terminal, Diff, and Info tabs" /></div>
  <figcaption class="ms-shot__body">The three-zone session shell. Rail on the left, the agent transcript in the center, and a Terminal / Diff / Info work panel on the right. Drag the divider to split, or collapse the panel for a full-width read.</figcaption>
</figure>

The header wears one control for the session: a state-led title trigger, built in
[`context-bar.tsx`](repo:webapp/components/workspace/context-bar.tsx). The title shows the live agent or
lifecycle state; click it and a single popover opens with four sections. **Identity** names the branch
and its base, the agent and model, and the placement. **Changes** summarizes the ahead, behind, and
dirty counts as a glance echo of the Diff tab. **Actions** holds the reversible lifecycle verbs. **Danger
zone** holds the one destructive verb. There is no separate bar of lifecycle buttons; everything the
header needs is one click behind the title.

The transcript is a real chat panel, covered in the next section. To its right, the work panel
([`work-panel.tsx`](repo:webapp/components/workspace/work-panel.tsx)) carries three tabs:

| Tab | What it shows |
|---|---|
| **Terminal** | The agent's live tmux pane, mirrored in near real time with colors and box-drawing intact. It carries a permanent live pulse on its tab. |
| **Diff** | The change summary plus the full commit list for the workspace. |
| **Info** | A metrics one-liner, the agent and model identity, the placement, and the created and paused timestamps. |

On a wide screen you can split the transcript and the work panel side by side. The split is reachable but
never the default: open it from the view switcher
([`view-switcher.tsx`](repo:webapp/components/workspace/view-switcher.tsx)) and drag the handle to set the
ratio, which sticks. Every breakpoint otherwise opens on single-pane tabs, transcript-first once a
session resolves. Collapse the panel entirely (`⌘/Ctrl+J`) or send it full-screen when you want one
surface at full width.

---

## Steering the agent

The transcript in [`chat-panel.tsx`](repo:webapp/components/chat/chat-panel.tsx) shows the conversation as
it unfolds: your messages and the agent's replies, tool calls grouped into collapsible runs, and
background notifications. A composer floats at its foot. Type into it ("Steer the agent...") and your
message goes straight to the running agent as a follow-up, the same as typing into the terminal, just
from the browser.

**The steer composer never disables while the agent is working.** Steering a working agent with a
follow-up is the whole point, so the input stays live at all times. Enter sends, Shift+Enter makes a new
line. While the agent is working, a circular **Interrupt** (a Stop button) appears next to send. It is
the one emergency affordance: one click to stop the agent mid-turn, with no menu to dig through.

When the agent pauses to ask you something (an `AskUserQuestion`), you answer from the browser without
reaching for a terminal. The question renders inline in the transcript as an interactive card, built in
[`question-card.tsx`](repo:webapp/components/workspace/question-card.tsx). A single-choice question shows
its options as buttons, and tapping one sends the answer immediately. A multi-select or a batch of
several questions shows checkboxes and an explicit **Submit**. There is an "Other" free-text field for a
custom reply. The card mirrors the terminal's own answer grammar, so the same choice reaches the agent
whichever surface you answer from.

---

## Creating a workspace with the composer

The composer on the landing surface, built in
[`composer.tsx`](repo:webapp/components/composer/composer.tsx), is the single way to start a workspace.
There is no separate dialog. The first line of your prompt becomes the workspace title, and the full text
becomes the agent's first task as the session boots. Type a task and press Enter, and you land in the new
workspace.

<figure class="ms-shot">
  <div class="ms-shot__frame"><img loading="lazy" src="../img/screenshots/webapp-home-mobile.png" alt="The Grove web dashboard on a phone: the Hero composer and the recent-session strip, with the rail available behind the hamburger menu" /></div>
  <figcaption class="ms-shot__body">The dashboard on a phone. The composer, the recent sessions, and the live state are all there, and the rail is one tap behind the hamburger. No terminal required.</figcaption>
</figure>

A quiet chip row under the textarea carries an **Agent** picker and, for every agent that takes one, a
**Model** picker. The model picker's options come from the daemon's per-agent catalog, so what you can
pick is exactly what that agent supports, with a custom-id field as the escape hatch. The picker is
per-workspace: the model you choose is the one the new workspace launches with.

A context-chip row names the repository, the base, and the branch mode at a glance, with an **Advanced**
disclosure that reveals the rest: how the branch is made (*Auto branch*, where Grove names one from the
title and a timestamp; *New branch*, where you name it and pick a base; *Existing branch*, to check out a
local branch; *Track remote*, to track a remote branch; or *Repo root*, to work in the repo root with no
worktree), the base ref, and a *skip init* toggle. A fullscreen toggle opens a roomy Markdown editor with
a live preview for a longer brief, where ⌘/Ctrl+Enter creates. The composer speaks the same wire contract
as the TUI's create modal, so what you build in the browser is exactly what the engine builds.

**Lifecycle actions** live in the session identity popover, not the composer. *Pause*, *Resume*, and
*Respawn* fire straight away from the popover's Actions section, and the open popover re-renders the
swapped verb in place as the status flips. *Kill* is the one destructive verb, tucked in the Danger zone,
so it opens a confirm dialog with a delete-branch checkbox whose default follows where the branch came
from. Which verbs appear depends on the workspace's current status and placement, the same matrix the TUI
uses. The engine is the real precondition gate, so an action that cannot run surfaces a plain refusal
rather than doing something surprising.

---

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

---

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

---

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

---

## Reaching it from outside the network

The daemon's loopback bind is deliberate, so there is no blessed way to expose it directly. To reach a
remote host's dashboard, forward the web app's port over SSH.

```bash
ssh -N -L 3000:127.0.0.1:3000 you@remote-host
```

Then open <http://127.0.0.1:3000> on your own machine. You can also put a real tunnel in front of the
web app, such as Tailscale, WireGuard, or an authenticated reverse proxy. Widening the daemon's bind is
the wrong lever, because pairing assumes loopback. See the [security model](use-auth.md#the-security-model).

---

## See also

- [Authentication & pairing](use-auth.md): how a new device gets access.
- [CLI](use-cli.md): the `grove daemon serve` options and the `grove auth` group.
- [Agent activity and sessions](features-activity.md): the signals behind the live rail and the terminal pane.
- [Status semantics](features-status.md): what each glyph and color means.
- Building or hacking on the dashboard? That is the [`webapp/` README](https://github.com/bearlike/Grove/tree/current/webapp), the contributor surface, kept separate from this product guide.
</content>
</invoke>
