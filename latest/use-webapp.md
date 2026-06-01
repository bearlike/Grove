# Web dashboard

Grove is a terminal program first. A read-only web dashboard ships alongside it. With it you can glance
at your workspaces and watch each agent work from a phone or another machine on your network. The
dashboard never drives a workspace. It mirrors what the TUI already shows, and every lifecycle action
stays in the terminal. Think of it as a window onto the fleet, not a control panel.

<figure class="grove-shot" markdown>
  <span class="grove-shot__frame">
    ![Grove read-only web dashboard showing a workspace detail page with summary and live agent panels](img/screenshots/webapp-workspace-detail.png)
  </span>
  <p class="grove-shot__body">The workspace detail view. Identity on the left, the git summary in the middle, and the agent's live tmux pane on the right.</p>
</figure>

## What it shows

The home page is a grid of every workspace the daemon knows about, grouped by repository. It uses the
same status glyphs and colors as the TUI. Open a workspace and you get three panels that mirror the
TUI's three cards. The identity panel names the workspace, branch, base, agent, and status. The summary
panel carries ahead, behind, and dirty counts, the diff size, and the commits since the branch forked.
The agent panel mirrors the live tmux pane.

The list refreshes every few seconds, and the agent pane every couple of seconds. Polling pauses while
the browser tab is hidden, then resumes when you return. A status bar along the bottom reports the
daemon's health, uptime, and version.

The dashboard is read-only by design. There is no create, pause, resume, kill, respawn, edit, or
attach. Those actions live in the [TUI](use-tui.md) and the [CLI](use-cli.md). The browser is for
glancing, not for driving.

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
[`webapp/` README](https://github.com/bearlike/Grove/tree/main/webapp), not here.

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
- [Status semantics](features-status.md): what each glyph and color means.
- Building or hacking on the dashboard? That is the [`webapp/` README](https://github.com/bearlike/Grove/tree/main/webapp), the contributor surface, kept separate from this product guide.
