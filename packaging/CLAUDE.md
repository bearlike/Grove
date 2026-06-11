# packaging — systemd-user service units

> ↑ [root](../CLAUDE.md)

systemd-user service units for the Grove daemon and the optional webapp, plus the Makefile machinery that renders and installs them.

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

## Session lessons

_None yet._
