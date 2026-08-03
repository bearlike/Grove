# Grove systemd units

User-scope (`systemd --user`) unit files, installed via the repo `Makefile`.

| Unit | Default | Purpose |
|---|---|---|
| `grove-daemon.service` | always installed by `make systemd` | runs `grove daemon serve` on `127.0.0.1:7421` |
| `grove-webapp.service` | **opt-in** via `WITH_WEBAPP=1 make systemd` | runs `npm run start` for the Next.js dashboard, binds `0.0.0.0:3000` for LAN access |
| `grove-mcp.service` | **opt-in** via `WITH_MCP=1 make systemd` | runs `grove-mcp` over Streamable HTTP on `127.0.0.1:7431` for long-lived MCP clients |

## Quick reference

```bash
# Install daemon only (default)
make systemd

# Install daemon + webapp (opt-in)
WITH_WEBAPP=1 make systemd

# Install daemon + MCP over Streamable HTTP (opt-in)
WITH_MCP=1 make systemd

# Enable on login (and start now)
make systemd-enable
WITH_WEBAPP=1 make systemd-enable
WITH_MCP=1 make systemd-enable

# Stop + disable
make systemd-disable
WITH_WEBAPP=1 make systemd-disable
WITH_MCP=1 make systemd-disable

# Remove the unit files
make systemd-uninstall

# Show status / logs
make systemd-status
journalctl --user -u grove-daemon -f
journalctl --user -u grove-webapp -f
journalctl --user -u grove-mcp -f
```

## Overrides

Anything baked into the unit file is exposed as a Make variable:

| Variable | Default | Where it lands |
|---|---|---|
| `GROVE_BIN` | `command -v grove` | daemon ExecStart |
| `DAEMON_PATH` | invoking shell's `$PATH` | daemon `Environment=PATH=` |
| `DAEMON_HOST` | `127.0.0.1` | daemon `--host` |
| `DAEMON_PORT` | `7421` | daemon `--port`, webapp `GROVE_DAEMON_URL` |
| `WEBAPP_DIR` | `<repo>/webapp` | webapp `WorkingDirectory` |
| `NPM_BIN` | `command -v npm` | webapp ExecStart |
| `WEBAPP_HOST` | `0.0.0.0` | webapp `--hostname` (LAN reachable) |
| `WEBAPP_PORT` | `3000` | webapp `--port` |
| `MCP_BIN` | `command -v grove-mcp` | MCP ExecStart |
| `MCP_HOST` | `127.0.0.1` | MCP `--host` (loopback only) |
| `MCP_PORT` | `7431` | MCP `--port` |
| `SYSTEMD_USER_DIR` | `~/.config/systemd/user` | install destination |

```bash
DAEMON_PORT=7777 WEBAPP_PORT=3030 WITH_WEBAPP=1 make systemd
MCP_PORT=7500 WITH_MCP=1 make systemd
```

Reinstalling the unit files after a `make systemd` is safe — the recipes overwrite atomically and run `systemctl --user daemon-reload`. If a service is currently running, you'll need to `make systemd-disable && make systemd-enable` (or `systemctl --user restart`) for the new ExecStart to take effect.

### Verifying sessions survive an update

The daemon unit ships `KillMode=process` so restarting it (every Grove update runs `systemctl --user restart grove-daemon`) does **not** kill the shared tmux server it forked into its cgroup. Confirm the behavior:

```bash
tmux new-session -d -s survive && \
  systemctl --user restart grove-daemon && \
  tmux has-session -t survive && echo "survived"
```

`survive` dies with the old `KillMode=control-group` default and lives with `KillMode=process`.

## First-time webapp setup

The webapp service runs in production mode (`npm run start`), which needs a build artifact:

```bash
make webapp-build       # one-shot: npm ci + npm run build
WITH_WEBAPP=1 make systemd-enable
```

Re-run `make webapp-build` after pulling webapp changes; the service does not rebuild on its own.

## First-time MCP setup

`grove-mcp` is normally spawned per-connection over stdio by the MCP client and needs no unit at all. This service is for the other shape: **one long-lived server on Streamable HTTP**, so remote or multiple harnesses can share a single Grove instance.

A network transport binds a socket, so the server requires an inbound bearer token and **fails closed without one** — it exits 2 rather than publish workspace lifecycle control (create / kill / message) unauthenticated. Supply the token via an environment file:

```bash
install -d -m 700 ~/.config/grove
printf 'GROVE_MCP_TOKEN=%s\n' "$(openssl rand -hex 32)" > ~/.config/grove/mcp.env
chmod 600 ~/.config/grove/mcp.env
WITH_MCP=1 make systemd-enable
```

The unit reads it with `EnvironmentFile=-%h/.config/grove/mcp.env` — the leading `-` means a missing file is not a unit-level error (same pattern used for other Grove secret drop-ins), so an absent token surfaces as the service's own fail-closed exit in `journalctl --user -u grove-mcp`, not as a confusing systemd load failure.

Clients then present `Authorization: Bearer <token>` against `http://127.0.0.1:7431/mcp`. Keep the default loopback bind unless you have a specific reason to widen it — see below.

## Linger

If you want the daemon (and optionally the webapp) to survive logout — common when running on a remote host you'll SSH back into — enable user lingering once:

```bash
loginctl enable-linger $USER
```

Lingering is host-level, set once, independent of these unit files.

## Architecture rationale

- **systemd-user, not system-wide.** Same UID as the user's tmux server, same access to `~/.ssh`, `~/.grove`, git binary, no privilege escalation. The daemon is a personal tool, not infrastructure.
- **Webapp opt-in.** Most users only need the TUI. Installing the webapp service by default would burn a port and run a Node process for users who never visit the dashboard.
- **MCP opt-in, and loopback by default.** Most users get MCP the stdio way, spawned per connection with no unit involved. When you do run it as a service, `MCP_HOST` defaults to `127.0.0.1` — deliberately unlike `WEBAPP_HOST=0.0.0.0`. The dashboard is read-only; the MCP surface can create, kill, and message workspaces, so exposing it off-host is an explicit operator decision, taken behind a tunnel, VPN, or TLS-terminating proxy.
- **No PATH bake on the MCP unit.** The daemon unit needs `Environment=PATH=` because it runs user-authored init scripts (issue #9). The MCP server only serves HTTP and proxies to the daemon — it shells out to nothing, so it inherits systemd's minimal environment without trouble.
- **`Wants=` not `Requires=`.** Daemon failure doesn't tear the webapp down. The webapp's status bar already surfaces "daemon unreachable" — failing closed loses signal without buying anything.
- **Production `npm run start`, not dev.** Dev mode runs hot-reload + telemetry overhead. For a host service you want the static-route, prebuilt bundle.
- **PATH baked into the units.** systemd-user inherits a minimal environment; the `Environment=PATH=...` lines are what make user-managed toolchains resolvable. The webapp unit needs `node`/`npm`; the daemon unit needs the *whole* dev PATH because it runs user-authored init scripts (pyenv/nvm/asdf shims and all — a bare PATH made `uv`/`npm` resolve to stale system binaries and rolled workspace creates back, issue #9). The daemon PATH is a snapshot of the shell that ran `make systemd`: re-run `make systemd` after toolchain moves (e.g. an nvm default-version bump), or pin one explicitly with `DAEMON_PATH=... make systemd`.
