# Web dashboard

Grove is a terminal program first, but a read-only web dashboard ships
alongside it so you can glance at your workspaces — and watch each agent's
live output — from a phone or any other machine on your network. The
dashboard never drives a workspace; it mirrors what the TUI already shows,
and every lifecycle action stays in the terminal.

<figure class="grove-shot" markdown>
  <span class="grove-shot__frame">
    ![Grove read-only web dashboard showing a workspace detail page with summary and live agent panels](img/screenshots/webapp-workspace-detail.png)
  </span>
  <p class="grove-shot__body">The workspace detail view: identity on the left, the git summary in the middle, and the agent's live tmux pane on the right.</p>
</figure>

## What it shows

The home page is a grid of every workspace the daemon knows about, faceted
by repository, painted with the same status glyphs and polarity-aware stat
colours as the TUI. Opening a workspace gives you three panels that mirror
the TUI's three cards: an *identity* panel (title, branch, base, agent,
status), a *summary* panel (ahead, behind, dirty, diff counts, and the
commits since the branch forked), and an *agent* panel that mirrors the live
tmux pane. The list refreshes every few seconds and the agent pane every
couple of seconds; polling pauses while the browser tab is hidden and
resumes when you return to it. A persistent status bar reports the daemon's
health, uptime, and version.

The dashboard is read-only by design. There is no create, pause, resume,
kill, respawn, edit, or attach — those actions live in the [TUI](use-tui.md)
and the [CLI](use-cli.md). The browser is for glancing, not driving.

## Running it

The dashboard is two processes: the **daemon**, which exposes Grove's engine
over HTTP, and the **web app**, a Next.js server that the browser actually
talks to. Build the web app once, then run both:

```bash
# Build the web app once (repeat after each upgrade)
cd webapp && npm install && npm run build

# Run the two processes
grove daemon serve     # terminal 1 — loopback, port 7421
npm run start          # terminal 2 (from webapp/) — serves the build on 0.0.0.0:3000
```

Open <http://127.0.0.1:3000> on the same machine. Because the web app binds
`0.0.0.0`, any phone or laptop on the same network reaches it at
`http://<machine-ip>:3000`. If the daemon runs on a non-default host or port,
point the web app at it with `GROVE_DAEMON_URL` in `webapp/.env.local`. The
first time a new device connects it has to [pair](use-auth.md) with the host.

Editing the dashboard's own code is a separate, contributor workflow — the
development server, wire-type codegen, and the test suites are documented in
the [`webapp/` README](https://github.com/bearlike/Grove/tree/main/webapp),
not here.

## How it stays loopback-only

```
Browser (LAN:3000)  ──http──▶  Next.js (0.0.0.0:3000)  ──http──▶  Daemon (127.0.0.1:7421)
                                 │ /api/grove/*  (BFF proxy)
```

The browser only ever calls the web app's own origin (`/api/grove/*`), so
there is no CORS to configure. The Next.js server acts as a
backend-for-frontend: it proxies those calls to the daemon at
`GROVE_DAEMON_URL` (default `http://127.0.0.1:7421`) and attaches the paired
session's token server-side, so the daemon's token never reaches the
browser. The daemon itself stays bound to loopback and never needs to be
exposed on the network — only the web app does. That separation is the whole
security story; [authentication & pairing](use-auth.md) covers the rest.

## Always-on with systemd

To keep both processes running across reboots on a Linux host, build the web
app once and render the user units with the web app opted in:

```bash
make webapp-build                 # npm ci + npm run build
WITH_WEBAPP=1 make systemd        # write grove-daemon + grove-webapp units
WITH_WEBAPP=1 make systemd-enable # reload, enable, start now
loginctl enable-linger "$USER"    # survive logout (remote hosts)
```

The web app unit `Wants` the daemon rather than `Requires` it, so a daemon
hiccup leaves the dashboard up to report "unreachable" instead of being torn
down with it. The service runs the production build and does not rebuild
itself — after pulling new source, run
`make webapp-build && systemctl --user restart grove-webapp`. Ports are
overridable: `DAEMON_PORT=7777 WEBAPP_PORT=3030 WITH_WEBAPP=1 make systemd`.

## Reaching it from outside the network

The daemon's loopback bind is deliberate, so there is no blessed way to
expose it directly. To reach a remote host's dashboard, forward the web
app's port over SSH —

```bash
ssh -N -L 3000:127.0.0.1:3000 you@remote-host
```

— and open <http://127.0.0.1:3000> locally, or put a real tunnel (Tailscale,
WireGuard, an authenticated reverse proxy) in front of the web app. Widening
the daemon's bind is the wrong lever; pairing assumes loopback. See the
[security model](use-auth.md#the-security-model).

## See also

- [Authentication & pairing](use-auth.md): how a new device gets access.
- [CLI](use-cli.md): `grove daemon serve` options and the `grove auth` group.
- [Status semantics](features-status.md): what each glyph and colour means.
- Building or hacking on the dashboard? That's the [`webapp/` README](https://github.com/bearlike/Grove/tree/main/webapp) — the contributor surface, kept separate from this product guide.
