# packaging — systemd-user service units + clean-install smoke

> ↑ [root](../CLAUDE.md)

systemd-user service units for the Grove daemon and the optional webapp, plus the Makefile machinery that renders and installs them, and the Docker clean-install smoke harness.

## Templates & targets

**Ship units as `*.service.in` templates; render them with Makefile sed substitution.** `packaging/systemd/` holds the templates. The root `Makefile` carries the operator targets: `systemd`, `systemd-enable`, `systemd-disable`, `systemd-uninstall`, `systemd-status`, `systemd-print`.

**Flow all ports/hosts/paths through Make variables, auto-detected via `command -v`.** Never hard-code an install path or port in a template.

## Install policy

**Default daemon-only; webapp install is opt-in via `WITH_WEBAPP=1`.** Most users don't run the dashboard.

**The webapp unit `Wants=` the daemon, never `Requires=`.** A daemon failure must not tear the webapp down — the in-app status bar surfaces "unreachable" instead. Use `Wants=` for any future companion service that merely polls or talks to the daemon.

## Adding a new unit

**Follow the established shape: ship a `.in` template, add a `_systemd-install-<name>` recipe, gate inclusion via a Make-level `$(if $(WITH_<NAME>),...)` conditional.** Never gate with a shell `if`/`fi` inside a recipe — multi-line shell heredocs inside Make `define` blocks fail in subtle ways.

## Testing

**Tests assert against `make systemd-print`, never the live filesystem.** `tests/test_systemd_packaging.py` invokes the print target and asserts placeholder substitution plus the `Wants=` invariant. **Never write to `~/.config/systemd/user` from a test.** See [tests](../tests/CLAUDE.md) for the print-target testing rule.

## Clean-install smoke (`docker/`)

**`packaging/docker/` proves the zero-touch fresh-install path; `make install-smoke` runs it.** The Dockerfile is a deps-only image (fresh Ubuntu, git/tmux/jq/curl, uv + a managed CPython, the stock non-root `ubuntu` user — deliberately NO Grove baked in); `smoke.sh` installs Grove at test time via `uv tool install` from the read-only-mounted checkout and asserts the whole first run with zero human input: version, graceful not-a-repo error, `config init` scaffold, zero-config create/kill on the built-in `shell` agent, daemon pair → `grove auth approve` → authenticated list, and the `initialized <dir>` first-run log lines. `GROVE_INSTALL_SPEC` swaps the install source (e.g. the public `git+...@current` URL) for release-mode verification. Born from issue #105: the PyPI name `grove` is an unrelated package, so installers must always use the full git spec — `tests/test_install_scripts.py` pins that contract on the script text; the container proves it end-to-end.

## Session lessons

- **The daemon unit bakes an install-time PATH via `@DAEMON_PATH@` (issue #9, 2026-06-11).** `systemd --user` never sources shell rc files, so without `Environment=PATH=` the daemon ran user init scripts against a bare PATH and pyenv/nvm/asdf toolchains resolved to stale system binaries. `DAEMON_PATH` defaults to the PATH of the shell running `make systemd` — snapshot semantics, so toolchain moves (e.g. an nvm default bump) need a `make systemd` re-run, not a hand-edited drop-in. Any future unit that executes user-authored commands needs the same line.
