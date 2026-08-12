"""Explicit historical usage projection and optional telemetry backfill CLI."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timedelta

import typer

from grove.core.config import load_config
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.usage import UsageService

usage_app = typer.Typer(
    name="usage",
    help="Project historical agent usage and optionally export selected telemetry.",
    no_args_is_help=True,
)


@usage_app.command("backfill")
def backfill(
    *,
    telemetry: bool = typer.Option(
        False,
        "--telemetry",
        help="Also export profiles explicitly selected in telemetry.backfill.profiles.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Plan from the current cache without local or remote writes."
    ),
    force: bool = typer.Option(
        False, "--force", help="Reproject unchanged local transcript files before export."
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Confirm the external writes made by --telemetry."
    ),
    limit: int = typer.Option(10_000, min=1, help="Maximum immutable traces to export."),
    settled_minutes: int = typer.Option(
        60,
        min=1,
        help="Exclude sessions updated more recently than this safety window.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Render the structured result as JSON."),
) -> None:
    """Backfill the local projection; telemetry is a separate explicit opt-in."""
    if telemetry and not dry_run and not yes:
        typer.secho("--telemetry makes external writes; pass --yes to confirm", err=True, fg="red")
        raise typer.Exit(code=2)
    cfg = load_config(repo_root=None)
    if telemetry and not cfg.telemetry.backfill.enabled:
        typer.secho("telemetry.backfill.enabled is false", err=True, fg="red")
        raise typer.Exit(code=2)
    if telemetry and not cfg.telemetry.backfill.profiles:
        typer.secho("telemetry.backfill.profiles selects no profiles", err=True, fg="red")
        raise typer.Exit(code=2)

    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(), config_loader=load_config)
    service = UsageService(cfg=cfg, registry=registry)
    try:
        local = None if dry_run else service.refresh(force=force)
        remote = (
            service.telemetry_backfill(
                dry_run=dry_run,
                limit=limit,
                settled_for=timedelta(minutes=settled_minutes),
            )
            if telemetry
            else None
        )
    finally:
        service.close()

    payload = {
        "local": local.model_dump(mode="json") if local is not None else None,
        "telemetry": asdict(remote) if remote is not None else None,
        "dry_run": dry_run,
    }
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        return
    if local is not None:
        typer.echo(
            f"Local projection: {local.indexed_sources} sources, "
            f"{local.changed_sources} changed in {local.duration_ms} ms"
        )
    if remote is not None:
        action = "planned" if dry_run else "exported"
        typer.echo(
            f"Telemetry {action}: {remote.planned_traces} traces / "
            f"{remote.planned_spans} observations; {remote.completed_traces} completed, "
            f"{remote.submitted_traces} submitted, "
            f"{remote.checkpoint_skips} checkpointed, {remote.degraded_traces} degraded, "
            f"{remote.ownership_skips} ownership-skipped"
        )


__all__ = ["usage_app"]
