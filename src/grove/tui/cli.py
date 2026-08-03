"""Typer CLI for Grove. Default command runs the TUI; subcommands surface
read-only views from the engine without launching Textual."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import UUID

import typer
from loguru import logger

from grove import __version__
from grove.core import GroveError, build, load_config, paths
from grove.core.agents.hook import run_hook_from_stdin
from grove.core.config import add_known_project, dump_config_json, dump_schema_json, write_schema
from grove.core.git import detect_root
from grove.tui.cli_agent import register as register_agent_commands
from grove.tui.cli_code import register as register_code_commands
from grove.tui.cli_doctor import doctor_app
from grove.tui.cli_onboarding import (
    AgentChoice,
    render_outcomes,
    resolve_agents,
    run_onboarding,
)
from grove.tui.cli_onboarding import register as register_onboarding_commands
from grove.tui.cli_sessions import sessions_app
from grove.tui.cli_shell import register as register_shell_commands
from grove.tui.cli_workspace import register as register_workspace_commands

app = typer.Typer(
    name="grove",
    help="Tend git worktrees + tmux sessions like branches in a forest.",
    no_args_is_help=False,
)

config_app = typer.Typer(
    name="config",
    help="Inspect and scaffold Grove configuration files.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")

auth_app = typer.Typer(
    name="auth",
    help="Approve / deny pairing requests and manage active sessions.",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")
app.add_typer(sessions_app, name="sessions")
app.add_typer(doctor_app, name="doctor")

# Flat workspace verbs (`grove create` / `grove message`) — grafted on like
# `ls`/`version` rather than nested under a `workspace` subgroup.
register_workspace_commands(app)

# `grove skills install` / `grove mcp install` — onboard Claude/Codex.
register_onboarding_commands(app)


# `grove code` — open a containerized workspace's container in VS Code.
register_code_commands(app)


# `grove shell` — an interactive, persistent shell inside that container.
register_shell_commands(app)


# `grove agent …` — several agents in one container. Its own noun
# rather than an `--agent` flag on every existing verb: a single-agent workspace
# is the overwhelming case and keeps every verb's signature untouched.
register_agent_commands(app)


# ─── default command (TUI) ──────────────────────────────────────────────────


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Default entry: launch the TUI when no subcommand is given."""
    _configure_logging()
    if ctx.invoked_subcommand is not None:
        return
    # Lazy import: subcommands like `version` shouldn't pay the textual import cost.
    from grove.tui.app import GroveApp  # noqa: PLC0415

    try:
        manager = build()
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    GroveApp(manager).run()


# ─── ls ─────────────────────────────────────────────────────────────────────


@app.command("ls")
def list_workspaces() -> None:
    """Print this repo's workspaces as JSON."""
    try:
        manager = build()
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    states = manager.list()
    payload = [
        {
            "id": s.id,
            "title": s.title,
            "agent": s.agent_name,
            "branch": s.branch,
            "status": s.status.value,
            "worktree_path": s.worktree_path,
            "tmux_session": s.tmux_session,
            "runtime": s.runtime.value,
            "runtime_fallback_reason": s.runtime_fallback_reason,
            "runtime_default_config": s.runtime_default_config,
            "runtime_no_tmux": s.runtime_no_tmux,
        }
        for s in states
    ]
    typer.echo(json.dumps(payload, indent=2))


# ─── config subgroup ────────────────────────────────────────────────────────


@config_app.command("show")
def config_show() -> None:
    """Print the merged effective config for the current repo."""
    try:
        cfg = load_config(detect_root(Path.cwd()))
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(dump_config_json(cfg))


_PROJECT_PATH_ARGUMENT = typer.Argument(
    None, help="Repo path (default: the current directory's repo)."
)


@config_app.command("add-project")
def config_add_project(
    path: Path | None = _PROJECT_PATH_ARGUMENT,
) -> None:
    """Register a git repo in the user config's known-projects list.

    Adds ``path`` (default: cwd) to ``GroveConfig.projects`` so it shows up in
    every cross-project surface (dashboard, project picker, ``known_roots()``)
    even with zero workspaces — without needing its own committed
    ``.grove/config.json``.
    """
    repo_root = detect_root(path or Path.cwd())
    if repo_root is None:
        typer.secho("not in a git repository", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    target = paths.user_config_path()
    try:
        added = add_known_project(repo_root, target=target)
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if added:
        typer.secho(f"added {repo_root} to {target}", fg=typer.colors.GREEN)
    else:
        typer.echo(f"{repo_root} is already known ({target})")


@config_app.command("init")
def config_init(
    *,
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing config file."),
    with_onboarding: bool = typer.Option(
        False,
        "--with-onboarding",
        help=(
            "Also install Grove's skills and register Grove as an MCP "
            "server for this project, non-interactively (see `grove skills "
            "install` / `grove mcp install` to run either on demand)."
        ),
    ),
) -> None:
    """Scaffold a project config at <repo>/.grove/config.json."""
    repo_root = detect_root(Path.cwd())
    if repo_root is None:
        typer.secho("not in a git repository", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    target = paths.project_config_path(repo_root)
    if target.exists() and not force:
        typer.secho(
            f"{target} already exists; pass --force to overwrite", fg=typer.colors.YELLOW, err=True
        )
        raise typer.Exit(code=1)

    schema_target = paths.user_schema_path()
    schema_path = write_schema(schema_target)
    try:
        rel_schema = os.path.relpath(schema_path, target.parent)
    except ValueError:
        rel_schema = str(schema_path)

    stub = {
        "$schema": rel_schema,
        "worktree": {
            "root_template": "${repo}/.worktrees",
            "branch_prefix": "grove/",
        },
        "agents": [
            {"name": "claude", "command": "claude", "description": "Anthropic Claude Code"},
        ],
        "init_script": {
            "enabled": False,
        },
    }
    paths.ensure_dir(target.parent)
    target.write_text(json.dumps(stub, indent=2) + "\n", encoding="utf-8", newline="\n")
    typer.echo(f"wrote {target}")
    typer.echo(f"schema:  {schema_path}")

    if with_onboarding:
        typer.echo("\nonboarding:")
        outcomes = run_onboarding(
            actions=["skill", "mcp"],
            targets=["project"],
            agents=resolve_agents(AgentChoice.all),
            repo_root=repo_root,
        )
        render_outcomes(outcomes)


@config_app.command("schema")
def config_schema(
    *,
    stdout: bool = typer.Option(
        False,
        "--stdout",
        help="Print the JSON Schema to stdout instead of writing to disk.",
    ),
) -> None:
    """Write the JSON Schema next to the user config (for IDE autocomplete).

    Pass --stdout to print the schema instead — the docs-build pipeline pipes
    this output into a hook that renders the configuration-reference page,
    so the docs always match the live Pydantic model.
    """
    if stdout:
        typer.echo(dump_schema_json(), nl=False)
        return
    path = write_schema()
    typer.echo(f"wrote {path}")


# ─── version + debug ────────────────────────────────────────────────────────


@app.command("version")
def show_version() -> None:
    """Print the installed Grove version."""
    typer.echo(f"grove {__version__}")


@app.command("agent-hook", hidden=True)
def agent_hook() -> None:
    """Internal: Claude Code status hook.

    Reads one hook event as JSON on stdin and writes a per-session status sidecar
    the Activity Dashboard reads for push status (precise BLOCKED). Installed via
    ``claude --settings`` when ``hooks.enabled`` — not meant to be run by hand.
    """
    raise typer.Exit(run_hook_from_stdin())


@app.command("debug")
def debug() -> None:
    """Print resolved paths used by Grove."""
    repo_root = detect_root(Path.cwd())
    try:
        cfg = load_config(repo_root)
        config_loaded = True
    except GroveError:
        cfg = None
        config_loaded = False
    del cfg  # we only report whether load succeeded

    typer.echo(
        json.dumps(
            {
                "user_config_path": str(paths.user_config_path()),
                "user_state_path": str(paths.user_state_path()),
                "user_schema_path": str(paths.user_schema_path()),
                "project_config_path": (
                    str(paths.project_config_path(repo_root)) if repo_root else None
                ),
                "project_local_config_path": (
                    str(paths.project_local_config_path(repo_root)) if repo_root else None
                ),
                "repo_root": str(repo_root) if repo_root else None,
                "config_loaded": config_loaded,
            },
            indent=2,
        )
    )


# ─── auth subgroup ──────────────────────────────────────────────────────────


def _auth_store() -> object:
    """Local helper: build a SessionStore using the user's resolved auth path.

    Returns ``object`` to keep the type annotation cheap; callers know
    they're getting a ``SessionStore``. The deferred import keeps Typer's
    help generation snappy when the subcommand isn't used.
    """
    from grove.core.auth import SessionStore  # noqa: PLC0415

    return SessionStore()


@auth_app.command("pending")
def auth_pending() -> None:
    """List pending pairing requests waiting for approval."""
    store = _auth_store()
    challenges = store.list_pending_challenges()  # type: ignore[attr-defined]
    if not challenges:
        typer.echo("no pending pairings")
        return
    for c in challenges:
        typer.echo(
            f"{c.challenge_id}  code={c.code}  label={c.label!r}  "
            f"state={c.state.value}  expires_at={c.expires_at.isoformat()}"
        )


@auth_app.command("approve")
def auth_approve(
    challenge_id: str = typer.Argument(..., help="Challenge id from `auth pending`."),
) -> None:
    """Approve a pending pairing request. The requesting client picks up the
    token via its own polling endpoint — this command never prints a token."""
    try:
        cid = UUID(challenge_id)
    except ValueError as exc:
        typer.secho(f"invalid challenge id: {challenge_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    store = _auth_store()
    try:
        challenge = store.pair_approve(cid)  # type: ignore[attr-defined]
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"approved {challenge.challenge_id} (label={challenge.label!r})")


@auth_app.command("deny")
def auth_deny(
    challenge_id: str = typer.Argument(..., help="Challenge id from `auth pending`."),
) -> None:
    """Deny a pending pairing request."""
    try:
        cid = UUID(challenge_id)
    except ValueError as exc:
        typer.secho(f"invalid challenge id: {challenge_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    store = _auth_store()
    try:
        challenge = store.pair_deny(cid)  # type: ignore[attr-defined]
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"denied {challenge.challenge_id} (label={challenge.label!r})")


@auth_app.command("sessions")
def auth_sessions() -> None:
    """List currently-active sessions (revoked sessions hidden)."""
    store = _auth_store()
    sessions = store.list_sessions()  # type: ignore[attr-defined]
    if not sessions:
        typer.echo("no active sessions")
        return
    for s in sessions:
        typer.echo(
            f"{s.session_id}  label={s.label!r}  "
            f"created_at={s.created_at.isoformat()}  expires_at={s.expires_at.isoformat()}"
        )


@auth_app.command("revoke")
def auth_revoke(
    session_id: str = typer.Argument(..., help="Session id from `auth sessions`."),
) -> None:
    """Revoke an active session (kicks that device until it pairs again)."""
    try:
        sid = UUID(session_id)
    except ValueError as exc:
        typer.secho(f"invalid session id: {session_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    store = _auth_store()
    try:
        store.revoke(sid)  # type: ignore[attr-defined]
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"revoked {session_id}")


# ─── helpers ────────────────────────────────────────────────────────────────


def _configure_logging() -> None:
    """Route loguru output to stderr at WARNING by default; DEBUG if GROVE_DEBUG."""
    level = "DEBUG" if os.environ.get("GROVE_DEBUG") else "WARNING"
    logger.remove()
    logger.add(sys.stderr, level=level, colorize=True)
