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

from grove.core import GroveError, SessionExplorer, SessionListing
from grove.core.agents import SessionTurn

sessions_app = typer.Typer(
    name="sessions",
    help="List, read, and dump agent sessions across this project's worktrees.",
    no_args_is_help=True,
)

_SINCE_PATTERN = re.compile(r"^(\d+)\s*([mhdw])$")
_SINCE_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_TABLE_TEXT_CAP = 60
_SHOW_TEXT_CAP = 4000
# Same prompt glyph the TUI uses; deliberate, not a mistyped ">".
_PROMPT_GLYPH = "❯"  # noqa: RUF001


def _explorer() -> SessionExplorer:
    try:
        return SessionExplorer.from_cwd(Path.cwd())
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


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
        "transcript_path": str(s.transcript_path),
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


def _turn_payload(turn: SessionTurn) -> dict[str, Any]:
    return {
        "user_text": turn.user_text,
        "started_at": turn.started_at.isoformat() if turn.started_at else None,
        "entries": [{"role": e.role, "text": e.text} for e in turn.entries],
    }


@sessions_app.command("list")
def list_sessions(
    *,
    agent: str | None = typer.Option(
        None, "--agent", help="Only sessions from this adapter kind (e.g. claude_code)."
    ),
    workspace: str | None = typer.Option(
        None, "--workspace", "-w", help="Workspace id prefix or title substring."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Only sessions modified since (30m/6h/2d/1w or ISO date)."
    ),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Keep the newest N rows."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """List every agent session across this project's worktrees, newest first."""
    explorer = _explorer()
    listings = explorer.list(
        agent=agent,
        workspace=workspace,
        since=_parse_since(since) if since else None,
        limit=limit,
    )
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
    ref: str = typer.Argument(..., help="Session id or unique prefix (see `sessions list`)."),
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


@sessions_app.command("dump")
def dump_session(
    ref: str = typer.Argument(..., help="Session id or unique prefix (see `sessions list`)."),
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


__all__ = ["sessions_app"]
