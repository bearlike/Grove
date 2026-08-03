# packaging — systemd-user service units + clean-install smoke

> ↑ [root](../CLAUDE.md)

systemd-user service units for the Grove daemon and the optional webapp, plus the Makefile machinery that renders and installs them, and the Docker clean-install smoke harness.

## Templates & targets

**Ship units as `*.service.in` templates; render them with Makefile sed substitution.** `packaging/systemd/` holds the templates. The root `Makefile` carries the operator targets: `systemd`, `systemd-enable`, `systemd-disable`, `systemd-uninstall`, `systemd-status`, `systemd-print`.

**Flow all ports/hosts/paths through Make variables, auto-detected via `command -v`.** Never hard-code an install path or port in a template.

## Install policy

**Default daemon-only; webapp install is opt-in via `WITH_WEBAPP=1`, networked MCP via `WITH_MCP=1`.** Most users don't run the dashboard, and `grove-mcp` needs a unit *only* when serving its network transport — under stdio the MCP client spawns it per connection, so there is nothing to supervise.

**The webapp unit `Wants=` the daemon, never `Requires=`.** A daemon failure must not tear the webapp down — the in-app status bar surfaces "unreachable" instead. Use `Wants=` for any future companion service that merely polls or talks to the daemon. `grove-mcp.service` follows the same rule.

**A unit whose service needs a secret takes it via `EnvironmentFile=-%h/.config/grove/<name>.env` (mode 0600), never `Environment=`.** The leading `-` is deliberate: a missing file must not be a *unit load* error, so an absent secret surfaces as the service's own fail-closed exit in the journal — a message that names the fix — rather than a systemd error that names only the file. `grove-mcp.service` sources `GROVE_MCP_TOKEN` this way, matching how the Gotify and LangFuse tokens already reach the daemon.

**Only the daemon needs `@DAEMON_PATH@`.** Bake a PATH into a unit *only* if that service executes user-authored commands (see the session lesson below). `grove-mcp` shells out to nothing, so a PATH line there would be cargo-culted surface that silently goes stale.

## Adding a new unit

**Follow the established shape: ship a `.in` template, add a `_systemd-install-<name>` recipe, gate inclusion via a Make-level `$(if $(WITH_<NAME>),...)` conditional.** Never gate with a shell `if`/`fi` inside a recipe — multi-line shell heredocs inside Make `define` blocks fail in subtle ways.

## Testing

**Tests assert against `make systemd-print`, never the live filesystem.** `tests/test_systemd_packaging.py` invokes the print target and asserts placeholder substitution plus the `Wants=` invariant. **Never write to `~/.config/systemd/user` from a test.** See [tests](../tests/CLAUDE.md) for the print-target testing rule.

**A Make default that mirrors a Python default needs a test binding the two.** A Makefile cannot import Python, so `MCP_HOST`/`MCP_PORT` and `McpServerConfig.DEFAULT_BIND_HOST`/`DEFAULT_PORT` are one policy written twice. `test_mcp_unit_defaults_track_the_cli_defaults` imports the constants and asserts the rendered unit carries them — without it, drift is silent and the unit would serve on a port that no doc, `--help` string, or client config agrees with. Add the equivalent test for any future duplicated default.

## Clean-install smoke (`docker/`)

**`packaging/docker/` proves the zero-touch fresh-install path; `make install-smoke` runs it.** The Dockerfile is a deps-only image (fresh Ubuntu, git/tmux/jq/curl, uv + a managed CPython, the stock non-root `ubuntu` user — deliberately NO Grove baked in); `smoke.sh` installs Grove at test time via `uv tool install` from the read-only-mounted checkout and asserts the whole first run with zero human input: version, graceful not-a-repo error, `config init` scaffold, zero-config create/kill on the built-in `shell` agent, daemon pair → `grove auth approve` → authenticated list, and the `initialized <dir>` first-run log lines. `GROVE_INSTALL_SPEC` swaps the install source (e.g. the public `git+...@current` URL) for release-mode verification. **The PyPI name `grove` is an unrelated package, so installers must always use the full git spec** — `tests/test_install_scripts.py` pins that contract on the script text; the container proves it end-to-end.

**A locked test environment and an unlocked install environment are two different dependency resolutions, and only the unlocked one is what users get.** CI's `uv sync --all-groups` resolves against `uv.lock`, so no test run can *ever* observe a newly-published incompatible major — an unbounded pin is invisible to the suite forever. `uv tool install` (the documented user path, and what this smoke runs) resolves fresh from PyPI and ignores the lockfile, which is how an upstream major that removed `mcp.server.fastmcp` reached users past every gate. Two consequences are baked in. The `unlocked-install` job in `ci.yml` stands in that second environment: it is **advisory on push/PR** (an upstream major is nobody's PR to fix, and a required check that reddens unrelated work gets routed around) and **blocking on a daily schedule**, because a major breaks the install path on the day it is *published*, not the day someone pushes. And both that job and this smoke install `[all]`, not the lean `[daemon]`: an extra that is never resolved is never install-tested. Probe every console script, not just `grove` — and note `grove-mcp --help` is **not** a sufficient probe: the FastMCP import is deferred (see the install-policy note above), so `--help` exits 0 even when the SDK is incompatible; only constructing `GroveMcpServer` touches it.

## Session lessons

- **The daemon unit sets `KillMode=process`.** libtmux's default `Server()` forks the shared user tmux server into the daemon's cgroup, so the systemd default (`control-group`) makes every `systemctl restart grove-daemon` — i.e. every Grove update — SIGTERM/SIGKILL the whole cgroup, killing the tmux server and all its sessions, external ones included. `process` signals only the daemon's main PID: the daemon is a control plane and sessions outlive it. `mixed` is wrong (its final SIGKILL still hits the cgroup). Not a socket problem — a custom tmux socket would break `grove attach`/`switch-client` and still wouldn't save Grove's own sessions.
- **The daemon unit bakes an install-time PATH via `@DAEMON_PATH@`.** `systemd --user` never sources shell rc files, so without `Environment=PATH=` the daemon runs user init scripts against a bare PATH and pyenv/nvm/asdf toolchains resolve to stale system binaries. `DAEMON_PATH` defaults to the PATH of the shell running `make systemd` — snapshot semantics, so toolchain moves (e.g. an nvm default bump) need a `make systemd` re-run, not a hand-edited drop-in. Any future unit that executes user-authored commands needs the same line.
