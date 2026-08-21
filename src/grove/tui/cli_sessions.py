"""`grove sessions` — explore agent sessions across a project's worktrees.

Thin Typer layer over :class:`grove.core.sessions.SessionExplorer`: the
explorer owns aggregation/filtering/resolution, this module owns rendering
(plain table or ``--json``) and flag parsing. Works from any directory inside
the project, including linked worktrees.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import humanize
import typer

from grove.core import GroveError, SessionExplorer, SessionListing, build
from grove.core.agents import SessionTurn
from grove.core.config import load_config
from grove.core.registry import RepoRegistry
from grove.core.sessions import CatalogEntry, SessionCatalog, SessionQuery
from grove.core.store import JsonWorkspaceStore
from grove.tui.cli_complete import Complete
from grove.tui.cli_workspace import clean_exit, resolve_workspace

sessions_app = typer.Typer(
    name="sessions",
    help="List, read, and dump agent sessions across this project's worktrees.",
    no_args_is_help=True,
)

_SINCE_PATTERN = re.compile(r"^(\d+)\s*([mhdw])$")
_SINCE_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_TABLE_TEXT_CAP = 60
_HOST_PROJECT_CAP = 19
_HOST_BRANCH_CAP = 15
_SHOW_TEXT_CAP = 4000
# Same prompt glyph the TUI uses; deliberate, not a mistyped ">".
_PROMPT_GLYPH = "❯"  # noqa: RUF001
# Same live-signal glyph the TUI's ACTIVE status uses (design-system.md) — no
# new vocabulary for "a runtime is actually attached to this cwd right now".
_LIVE_GLYPH = "●"


def _explorer() -> SessionExplorer:
    try:
        return SessionExplorer.from_cwd(Path.cwd())
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _catalog() -> SessionCatalog:
    """A host-wide catalog, independent of the current cwd's project.

    Mirrors the daemon's own host-wide construction (``_asgi.py``):
    ``load_config(repo_root=None)`` (user + built-in layers only, no project
    overlay — there is no single project here) plus the shared global store.
    """
    cfg = load_config(repo_root=None)
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(), config_loader=load_config)
    return SessionCatalog(registry)


def _parse_since(text: str) -> datetime:
    """``30m`` / ``6h`` / ``2d`` / ``1w`` relative forms, or an ISO date/datetime."""
    match = _SINCE_PATTERN.match(text.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return datetime.now(UTC) - timedelta(**{_SINCE_UNITS[unit]: amount})
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise typer.BadParameter(
            f"{text!r} is neither a relative window (30m/6h/2d/1w) nor an ISO date"
        ) from exc
    return parsed if parsed.tzinfo else parsed.astimezone()


def _truncate(text: str, cap: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _ago(when: datetime | None) -> str:
    if when is None:
        return "-"
    delta = datetime.now(UTC) - when
    return humanize.naturaldelta(delta) + " ago"


def _listing_payload(ls: SessionListing) -> dict[str, Any]:
    s = ls.summary
    return {
        "session_id": s.session_id,
        "agent": s.adapter_kind,
        "provenance": ls.provenance,
        "workspace_id": ls.workspace_id,
        "workspace_title": ls.workspace_title,
        "workspace_branch": ls.workspace_branch,
        "cwd": s.cwd,
        "git_branch": s.git_branch,
        # None (JSON null) for a remote-backed session — there is no local file.
        "transcript_path": str(s.transcript_path) if s.transcript_path is not None else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "modified_at": s.modified_at.isoformat() if s.modified_at else None,
        "size_bytes": s.size_bytes,
        "title": s.title,
        "first_prompt": s.first_prompt,
        "last_prompt": s.last_prompt,
        "state": s.activity.state.value,
        "human_turns": s.activity.human_turns,
        "assistant_replies": s.activity.assistant_replies,
        "tool_calls": s.activity.tool_calls,
        "model": s.activity.model,
    }


def _catalog_payload(entry: CatalogEntry) -> dict[str, Any]:
    ref = entry.ref
    project = entry.project
    return {
        "session_id": ref.session_id,
        "agent": ref.adapter_kind,
        "provenance": entry.provenance,
        "workspace_id": entry.workspace_id,
        "workspace_title": entry.workspace_title,
        "cwd": ref.cwd,
        "git_branch": ref.git_branch,
        "project": project.repo_name if project is not None else None,
        "project_root": str(project.repo_root) if project is not None else None,
        "is_grove_managed": project.is_grove_managed if project is not None else None,
        "transcript_path": str(ref.transcript_path) if ref.transcript_path is not None else None,
        "created_at": ref.birth.isoformat() if ref.birth else None,
        "modified_at": datetime.fromtimestamp(ref.mtime, tz=UTC).isoformat(),
        "live": entry.live,
    }


def _filter_catalog(
    entries: tuple[CatalogEntry, ...],
    *,
    agent: str | None,
    workspace: str | None,
    since: datetime | None,
    limit: int | None,
) -> list[CatalogEntry]:
    """Apply the same agent/workspace/since/limit filters `SessionExplorer.list`
    does, over catalog rows — filter-then-slice, so `--limit` still caps the
    filtered result rather than the pre-filter scan."""
    rows = list(entries)
    if agent is not None:
        rows = [e for e in rows if e.ref.adapter_kind == agent]
    if workspace is not None:
        needle = workspace.lower()
        rows = [
            e
            for e in rows
            if (e.workspace_id or "").startswith(workspace)
            or needle in (e.workspace_title or "").lower()
        ]
    if since is not None:
        since_ts = since.timestamp()
        rows = [e for e in rows if e.ref.mtime >= since_ts]
    return rows[:limit] if limit is not None else rows


def _print_host_table(entries: list[CatalogEntry]) -> None:
    header = (
        f"{'SESSION':<10} {'AGENT':<12} {'PROJECT':<20} {'BRANCH':<16} "
        f"{'WORKSPACE':<20} {'LIVE':<5} MODIFIED"
    )
    typer.echo(header)
    for entry in entries:
        ref = entry.ref
        # Honest fallback chain: a resolved project's name, else the bare
        # scanned directory, else "-" for the ~2% of sessions with no cwd at
        # all — never blank, never dropped.
        project_label = entry.project.repo_name if entry.project is not None else (ref.cwd or "-")
        workspace_label = entry.workspace_title or "-"
        modified = datetime.fromtimestamp(ref.mtime, tz=UTC)
        typer.echo(
            f"{ref.session_id[:8]:<10} "
            f"{ref.adapter_kind:<12} "
            f"{_truncate(project_label, _HOST_PROJECT_CAP):<20} "
            f"{_truncate(ref.git_branch or '-', _HOST_BRANCH_CAP):<16} "
            f"{_truncate(workspace_label, 19):<20} "
            f"{(_LIVE_GLYPH if entry.live else '-'):<5} "
            f"{_ago(modified)}"
        )


def _turn_payload(turn: SessionTurn) -> dict[str, Any]:
    return {
        "user_text": turn.user_text,
        "started_at": turn.started_at.isoformat() if turn.started_at else None,
        "entries": [{"role": e.role, "text": e.text} for e in turn.entries],
    }


def _query_payload(query: SessionQuery) -> dict[str, Any]:
    return {
        "ordinal": query.ordinal,
        "timestamp": query.timestamp.isoformat() if query.timestamp else None,
        "sent_at": query.sent_at.isoformat() if query.sent_at else None,
        "text": query.text,
    }


@sessions_app.command("list")
def list_sessions(
    *,
    host: bool = typer.Option(
        False,
        "--host",
        help=(
            "List across every repo this host has ever run an agent in — "
            "including ones not in cfg.projects — instead of just this project."
        ),
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="Only sessions from this adapter kind (e.g. claude_code).",
        autocompletion=Complete.adapter_kinds,
    ),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Workspace id prefix or title substring.",
        autocompletion=Complete.workspaces,
    ),
    since: str | None = typer.Option(
        None, "--since", help="Only sessions modified since (30m/6h/2d/1w or ISO date)."
    ),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Keep the newest N rows."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """List every agent session across this project's worktrees, newest first.

    ``--host`` widens the scope to the whole host (the Session Catalog): a
    cheap, metadata-only scan across every repo an adapter has ever recorded
    a session for, so it renders PROJECT/BRANCH/LIVE columns instead of the
    project-scoped STATE/TURNS/TITLE ones — those need a full transcript
    parse, which the host-wide scan deliberately never pays per session.
    """
    since_dt = _parse_since(since) if since else None
    if host:
        entries = _filter_catalog(
            _catalog().scan(), agent=agent, workspace=workspace, since=since_dt, limit=limit
        )
        if as_json:
            typer.echo(json.dumps([_catalog_payload(e) for e in entries], indent=2))
            return
        if not entries:
            typer.echo("no sessions found")
            return
        _print_host_table(entries)
        return

    explorer = _explorer()
    listings = explorer.list(agent=agent, workspace=workspace, since=since_dt, limit=limit)
    if as_json:
        typer.echo(json.dumps([_listing_payload(ls) for ls in listings], indent=2))
        return
    if not listings:
        typer.echo("no sessions found")
        return
    header = (
        f"{'SESSION':<10} {'AGENT':<12} {'WORKSPACE':<20} {'STATE':<8} "
        f"{'TURNS':>5} {'MODIFIED':<16} TITLE / PROMPT"
    )
    typer.echo(header)
    for ls in listings:
        s = ls.summary
        label = s.title or s.last_prompt or s.first_prompt or ""
        workspace_label = ls.workspace_title or ls.workspace_branch or "-"
        typer.echo(
            f"{s.session_id[:8]:<10} "
            f"{s.adapter_kind:<12} "
            f"{_truncate(workspace_label, 19):<20} "
            f"{s.activity.state.value:<8} "
            f"{s.activity.human_turns:>5} "
            f"{_ago(s.modified_at):<16} "
            f"{_truncate(label, _TABLE_TEXT_CAP)}"
        )


@sessions_app.command("show")
def show_session(
    ref: str = typer.Argument(
        ...,
        help="Session id or unique prefix (see `sessions list`).",
        autocompletion=Complete.sessions,
    ),
    *,
    last: int | None = typer.Option(None, "--last", "-l", help="Only the most recent N turns."),
    as_json: bool = typer.Option(False, "--json", help="Emit structured turns as JSON."),
) -> None:
    """Print a session's conversation as normalized turns (oldest first)."""
    explorer = _explorer()
    try:
        listing = explorer.resolve(ref)
        turns = explorer.turns(listing.summary.session_id, last=last)
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        payload = {
            "session": _listing_payload(listing),
            "turns": [_turn_payload(t) for t in turns],
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    s = listing.summary
    head = [s.session_id, s.adapter_kind]
    if listing.workspace_title:
        head.append(listing.workspace_title)
    if s.git_branch:
        head.append(s.git_branch)
    typer.echo(" · ".join(head))
    if s.title:
        typer.echo(f"title: {s.title}")
    for index, turn in enumerate(turns, start=1):
        when = turn.started_at.isoformat(timespec="seconds") if turn.started_at else ""
        typer.echo(f"\n── turn {index} {when}".rstrip())
        prompt = (
            _truncate(turn.user_text, _SHOW_TEXT_CAP)
            if turn.user_text
            else "(continuation — no prompt recorded)"
        )
        typer.echo(f"{_PROMPT_GLYPH} {prompt}")
        for entry in turn.entries:
            if entry.role == "tool":
                typer.echo(f"  ⚒ {entry.text}")
            else:
                typer.echo(f"  ⏺ {_truncate(entry.text, _SHOW_TEXT_CAP)}")


@sessions_app.command("recollect")
def recollect_session(
    session: str | None = typer.Argument(
        None,
        help=("Session id or unique prefix. Omit inside a workspace to read its primary session."),
        autocompletion=Complete.sessions,
    ),
    *,
    last: int | None = typer.Option(
        None, "--last", "-l", min=1, help="Keep only the most recent N queries."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit structured queries as JSON."),
) -> None:
    """Recover every direct user query from a complete session transcript.

    This lives under ``grove sessions`` rather than as a top-level verb: like
    ``list``, ``show``, and ``dump``, it is a drill-in over one session's
    recorded history. ``grove fleet`` is top-level because it is an explicitly
    host-wide, transcript-plus-git aggregation with a different cost class than
    cwd-bound workspace reads; recollection has neither property. Human slash
    commands count as queries because they are direct instructions; harness and
    provider envelopes do not. With no session argument, the current workspace's
    primary readable session is used.
    """
    explorer = _explorer()
    try:
        if session is None:
            from grove.tui.cli_workspace import resolve_or_infer_workspace  # noqa: PLC0415

            state = resolve_or_infer_workspace(build(), None)
            listing = explorer.primary_for_workspace(state.id)
        else:
            listing = explorer.resolve(session)
        queries = explorer.recollect_for(listing, last=last)
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps([_query_payload(query) for query in queries], indent=2))
        return
    if not queries:
        typer.echo("no direct user queries found")
        return
    for query in queries:
        when = query.timestamp.isoformat(timespec="seconds") if query.timestamp else ""
        typer.echo(f"\n── query {query.ordinal} {when}".rstrip())
        typer.echo(query.text)


@sessions_app.command("dump")
def dump_session(
    ref: str = typer.Argument(
        ...,
        help="Session id or unique prefix (see `sessions list`).",
        autocompletion=Complete.sessions,
    ),
    *,
    jsonl: bool = typer.Option(
        False, "--jsonl", help="Stream the raw transcript lines verbatim instead of JSON."
    ),
) -> None:
    """Dump a session's raw native records (main transcript + sub-agent files).

    Default output is one self-describing JSON object — the session id plus
    each transcript file's parsed records — so the shape is stable whether or
    not sub-agent files exist. ``--jsonl`` streams the original lines
    untouched (main transcript first, then sub-agent files).
    """
    explorer = _explorer()
    try:
        listing = explorer.resolve(ref)
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    paths = explorer.transcripts(listing)
    if not paths:
        typer.secho("no transcript files on disk yet", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=1)

    if jsonl:
        for path in paths:
            try:
                with path.open(encoding="utf-8") as fh:
                    for raw_line in fh:
                        line = raw_line.rstrip("\n")
                        if line.strip():
                            typer.echo(line)
            except OSError:
                continue
        return

    files: list[dict[str, Any]] = []
    for path in paths:
        records: list[Any] = []
        try:
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        records.append(json.loads(stripped))
                    except json.JSONDecodeError:
                        continue  # tolerate a truncated tail line, like the adapter does
        except OSError:
            continue
        files.append({"path": str(path), "records": records})
    typer.echo(
        json.dumps(
            {"session_id": listing.summary.session_id, "files": files},
            indent=2,
        )
    )


@sessions_app.command("remap")
def remap_session(
    workspace: str = typer.Argument(
        ...,
        help="Workspace id or unique id prefix (see `grove ls`).",
        autocompletion=Complete.workspaces,
    ),
    session: str = typer.Argument(
        ...,
        help="Session id or unique id prefix to pin as this workspace's primary.",
        autocompletion=Complete.sessions,
    ),
) -> None:
    """Pin an existing agent session as a workspace's tracked primary.

    The manual counterpart to Grove's automatic session tracking: point the
    dashboard at the right session after ``/clear`` rotated the id, or adopt a
    hand-started session as the workspace's own. Trusted and idempotent — the
    session ref (id or unique prefix) is resolved in the workspace's project.

    \b
      grove sessions remap a1b2 cafef00d
    """
    # One error funnel (#F10b): reuse cli_workspace.clean_exit — the same
    # GroveError → one-line-red + exit-1 contract every workspace verb uses —
    # instead of a hand-rolled try/except duplicating its body.
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        updated = manager.remap_session(state.id, session)
        typer.secho(
            f"remapped {updated.id} ({updated.title}) → session {updated.agent_session_id}",
            fg=typer.colors.GREEN,
        )


__all__ = ["sessions_app"]
