"""``grove doctor`` — render :class:`~grove.core.preflight.HostPreflight`.

Thin Typer layer, same shape as ``cli_sessions.py``: the engine
(``HostPreflight``) owns the checks, this module owns rendering (table or
``--json``) and the exit-code policy. Works from anywhere — the config
cascade needs a repo root only for its *project* layer, and doctor is a
host-level command, so a missing repo just means no project overlay (mirrors
``cli_sessions._catalog``'s host-wide construction).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from grove.core.config import load_config
from grove.core.git import GitRepo
from grove.core.preflight import CheckResult, HostPreflight

doctor_app = typer.Typer(
    name="doctor",
    help="Check host dependencies Grove's runtimes need (docker, tmux, git, agent CLIs, …).",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _payload(check: CheckResult) -> dict[str, object]:
    return {
        "name": check.name,
        "ok": check.ok,
        "detail": check.detail,
        "hint": check.hint,
        "required_for": check.required_for,
    }


def _mark(check: CheckResult) -> str:
    """The status cell. A failing OPTIONAL check is a warning, not a failure.

    Rendering it red would contradict the summary line directly below, which
    only counts required checks — and an operator who reads "FAIL" and then
    "all required checks passed" learns to distrust both.
    """
    if check.ok:
        return typer.style("ok", fg=typer.colors.GREEN)
    if check.required_for == "optional":
        return typer.style("warn", fg=typer.colors.YELLOW)
    return typer.style("FAIL", fg=typer.colors.RED)


def _print_table(checks: list[CheckResult]) -> None:
    header = f"{'STATUS':<6} {'CHECK':<24} {'SCOPE':<10} DETAIL"
    typer.echo(header)
    for check in checks:
        mark = _mark(check)
        typer.echo(f"{mark:<15} {check.name:<24} {check.required_for:<10} {check.detail}")
        if not check.ok and check.hint:
            typer.echo(f"       → {check.hint}")


@doctor_app.callback(invoke_without_command=True)
def doctor(
    *,
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Report host preflight status; never installs anything.

    Exit code 0 iff every ``required_for="all"`` check and the configured
    default runtime's checks (container if ``container.enabled``, else host)
    all pass — the same policy the create path's arm-4 probe applies.
    """
    cfg = load_config(repo_root=GitRepo.detect_root(Path.cwd()))
    preflight = HostPreflight(cfg)
    checks = preflight.all_checks()
    required = preflight.default_runtime_checks()
    overall_ok = all(c.ok for c in required)

    if as_json:
        typer.echo(
            json.dumps({"ok": overall_ok, "checks": [_payload(c) for c in checks]}, indent=2)
        )
    else:
        _print_table(checks)
        typer.echo("")
        typer.echo(
            typer.style("all required checks passed", fg=typer.colors.GREEN)
            if overall_ok
            else typer.style("one or more required checks failed", fg=typer.colors.RED)
        )

    raise typer.Exit(code=0 if overall_ok else 1)


__all__ = ["doctor_app"]
