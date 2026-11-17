"""``grove node`` — a thin shell over the daemon's ``/nodes`` routes.

Every verb is one SDK call, so the TUI, MCP and the web reach the same answers
through the same routes. Recovering a peer link (``reconnect``) happens in place
on the running daemon, never by restarting it.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

import typer

from grove.client import BackendConfig, GroveClient, GroveClientError
from grove.core.config import load_config
from grove.tui.cli_mailbox import DEFAULT_DAEMON_URL

node_app = typer.Typer(
    help=(
        "Federate this daemon with a hub: mint join tokens on the hub, join and "
        "check the link on a node. Back: grove --help."
    ),
    no_args_is_help=True,
)


def _call[T](operation: Callable[[GroveClient], Awaitable[T]]) -> T:
    client = GroveClient(
        BackendConfig(
            label="node",
            daemon_url=os.environ.get("GROVE_API_URL", DEFAULT_DAEMON_URL),
            daemon_token=os.environ.get("GROVE_API_TOKEN") or None,
        )
    )

    async def execute() -> T:
        await client.connect()
        try:
            return await operation(client)
        finally:
            await client.close()

    try:
        return asyncio.run(execute())
    except GroveClientError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _echo(model: Any) -> None:
    typer.echo(model.model_dump_json(indent=2))


@node_app.command("token")
def token() -> None:
    """On a hub: mint a one-time join token for one new node."""
    minted = _call(lambda client: client.mint_join_token())
    typer.echo(minted["token"])
    typer.secho(
        f"single use, expires in {minted['expires_in_seconds']}s; on the node run: "
        "grove node join <this hub's url> --token <token>",
        err=True,
    )


@node_app.command("join")
def join(
    hub_url: str = typer.Argument(..., help="The hub this node is configured for."),
    token: str = typer.Option(..., "--token", help="A token from `grove node token`."),
) -> None:
    """On a node: exchange a join token for this node's key, then register at once."""
    configured = load_config(repo_root=None).federation.node.hub_url
    if configured.rstrip("/") != hub_url.rstrip("/"):
        typer.secho(
            f"this daemon is configured for hub {configured or '(none)'!r}, not {hub_url!r}; "
            "set federation.node.hub_url in your config and restart the daemon first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    _echo(_call(lambda client: client.join_hub(token)))


@node_app.command("status")
def status() -> None:
    """This daemon's role and, on a node, its hub link."""
    _echo(_call(lambda client: client.node_self()))


@node_app.command("ls")
def ls() -> None:
    """On a hub: every admitted node, reachable or not."""
    nodes = _call(lambda client: client.list_nodes())
    typer.echo("[" + ",".join(n.model_dump_json() for n in nodes) + "]")


@node_app.command("ping")
def ping() -> None:
    """On a node: check the hub hop by hop: resolve, connect, then the key."""
    result = _call(lambda client: client.ping_hub())
    _echo(result)
    if not all(hop.ok for hop in result.hops):
        raise typer.Exit(code=1)


@node_app.command("drain")
def drain(
    undo: bool = typer.Option(False, "--undo", help="Accept new workspaces again."),
) -> None:
    """On a node: refuse new workspaces while running ones finish; the hub stops placing here."""
    _echo(_call(lambda client: client.drain_node(not undo)))


@node_app.command("reconnect")
def reconnect() -> None:
    """On a node: drop the link's current wait and re-register now."""
    _call(lambda client: client.reconnect_hub())
    typer.echo("reconnecting")


@node_app.command("forget")
def forget(node_id: str = typer.Argument(..., help="The node id `grove node ls` shows.")) -> None:
    """On a hub: revoke a node's key. Its next call is refused and its link ends."""
    _call(lambda client: client.forget_node(node_id))
    typer.echo(f"forgot {node_id}")
