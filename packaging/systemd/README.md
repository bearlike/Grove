# Grove systemd units

User-scope (`systemd --user`) unit files, installed via the repo `Makefile`.

| Unit | Default | Purpose |
|---|---|---|
| `grove-daemon.service` | always installed by `make systemd` | runs `grove daemon serve` on `127.0.0.1:7421` |
| `grove-webapp.service` | **opt-in** via `WITH_WEBAPP=1 make systemd` | runs `npm run start` for the Next.js dashboard, binds `0.0.0.0:3000` for LAN access |

## Quick reference

```bash
# Install daemon only (default)
make systemd

# Install daemon + webapp (opt-in)
WITH_WEBAPP=1 make systemd

# Enable on login (and start now)
make systemd-enable
WITH_WEBAPP=1 make systemd-enable

# Stop + disable
make systemd-disable
WITH_WEBAPP=1 make systemd-disable

# Remove the unit files
make systemd-uninstall

# Show status / logs
make systemd-status
journalctl --user -u grove-daemon -f
journalctl --user -u grove-webapp -f
```

## Overrides

Anything baked into the unit file is exposed as a Make variable:

| Variable | Default | Where it lands |
|---|---|---|
| `GROVE_BIN` | `command -v grove` | daemon ExecStart |
| `DAEMON_HOST` | `127.0.0.1` | daemon `--host` |
| `DAEMON_PORT` | `7421` | daemon `--port`, webapp `GROVE_DAEMON_URL` |
| `WEBAPP_DIR` | `<repo>/webapp` | webapp `WorkingDirectory` |
| `NPM_BIN` | `command -v npm` | webapp ExecStart |
| `WEBAPP_HOST` | `0.0.0.0` | webapp `--hostname` (LAN reachable) |
| `WEBAPP_PORT` | `3000` | webapp `--port` |
| `SYSTEMD_USER_DIR` | `~/.config/systemd/user` | install destination |

```bash
DAEMON_PORT=7777 WEBAPP_PORT=3030 WITH_WEBAPP=1 make systemd
```

Reinstalling the unit files after a `make systemd` is safe — the recipes overwrite atomically and run `systemctl --user daemon-reload`. If a service is currently running, you'll need to `make systemd-disable && make systemd-enable` (or `systemctl --user restart`) for the new ExecStart to take effect.

## First-time webapp setup

The webapp service runs in production mode (`npm run start`), which needs a build artifact:

```bash
make webapp-build       # one-shot: npm ci + npm run build
WITH_WEBAPP=1 make systemd-enable
```

Re-run `make webapp-build` after pulling webapp changes; the service does not rebuild on its own.

## Linger

If you want the daemon (and optionally the webapp) to survive logout — common when running on a remote host you'll SSH back into — enable user lingering once:

```bash
loginctl enable-linger $USER
```

Lingering is host-level, set once, independent of these unit files.

## Architecture rationale

- **systemd-user, not system-wide.** Same UID as the user's tmux server, same access to `~/.ssh`, `~/.grove`, git binary, no privilege escalation. The daemon is a personal tool, not infrastructure.
- **Webapp opt-in.** Most users only need the TUI. Installing the webapp service by default would burn a port and run a Node process for users who never visit the dashboard.
- **`Wants=` not `Requires=`.** Daemon failure doesn't tear the webapp down. The webapp's status bar already surfaces "daemon unreachable" — failing closed loses signal without buying anything.
- **Production `npm run start`, not dev.** Dev mode runs hot-reload + telemetry overhead. For a host service you want the static-route, prebuilt bundle.
- **PATH baked into the unit.** systemd-user inherits a minimal environment; the `Environment=PATH=...` line in the webapp unit is what makes `node`/`npm` resolvable when the user has nvm or asdf-managed Node.
