"""``grove auth login`` / ``logout``: sign a person in to the daemon with the OIDC device flow.

The device flow (RFC 8628) suits a terminal: Grove prints a short code and a
URL, the person approves it in any browser, and the CLI receives an ID token.
The daemon verifies that token and trades it for an ordinary Grove bearer
(``POST /auth/oidc/session``), which is saved 0600 beside ``auth.json`` and
used by every local client from then on, so the person's creates are theirs.
"""

from __future__ import annotations

import json
import time
from typing import Annotated

import httpx
import typer

from grove.core import paths

#: The grant type a device-code token request names (RFC 8628 section 3.4).
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def login(
    daemon: Annotated[
        str, typer.Option(help="The daemon to sign in to.")
    ] = "http://127.0.0.1:7421",
    label: Annotated[str, typer.Option(help="Names this sign-in in `grove auth sessions`.")] = (
        "cli"
    ),
) -> None:
    """Sign in through the OIDC issuer the daemon is configured with."""
    from grove.core.config import load_config  # noqa: PLC0415

    oidc = load_config(repo_root=None).auth.oidc
    if not oidc.issuer or not oidc.client_id:
        typer.echo("auth.oidc.issuer and auth.oidc.client_id are not configured", err=True)
        raise typer.Exit(code=2)
    with httpx.Client(timeout=30.0) as http:
        discovery = http.get(oidc.issuer.rstrip("/") + "/.well-known/openid-configuration")
        discovery.raise_for_status()
        meta = discovery.json()
        started = http.post(
            meta["device_authorization_endpoint"],
            data={"client_id": oidc.client_id, "scope": "openid profile"},
        )
        started.raise_for_status()
        grant = started.json()
        typer.echo(
            f"Open {grant.get('verification_uri_complete') or grant['verification_uri']} "
            f"and enter {grant['user_code']}"
        )
        id_token = _poll(http, meta["token_endpoint"], oidc.client_id, grant)
        signed = http.post(
            f"{daemon.rstrip('/')}/auth/oidc/session", json={"id_token": id_token, "label": label}
        )
    if signed.status_code != 200:
        typer.echo(f"the daemon refused the sign-in: {signed.text}", err=True)
        raise typer.Exit(code=1)
    body = signed.json()
    paths.write_atomic(paths.user_login_path(), json.dumps({"token": body["token"]}), mode=0o600)
    typer.echo(f"signed in as {body['subject']}")


def _poll(http: httpx.Client, token_url: str, client_id: str, grant: dict[str, object]) -> str:
    """Wait for the person to approve the code, at the interval the issuer asked for."""
    interval = float(str(grant.get("interval", 5)))
    deadline = time.monotonic() + float(str(grant.get("expires_in", 600)))
    while time.monotonic() < deadline:
        time.sleep(interval)
        answer = http.post(
            token_url,
            data={
                "grant_type": _DEVICE_GRANT,
                "device_code": grant["device_code"],
                "client_id": client_id,
            },
        )
        body = answer.json()
        if answer.status_code == 200:
            return str(body["id_token"])
        error = body.get("error")
        if error == "slow_down":
            interval += 5
        elif error != "authorization_pending":
            typer.echo(f"sign-in failed: {error}", err=True)
            raise typer.Exit(code=1)
    typer.echo("sign-in timed out before the code was approved", err=True)
    raise typer.Exit(code=1)


def logout() -> None:
    """Forget the saved sign-in; local clients go back to this host's own user."""
    paths.user_login_path().unlink(missing_ok=True)
    typer.echo("signed out")


def register(auth_app: typer.Typer) -> None:
    """Graft ``login`` and ``logout`` onto ``grove auth``."""
    auth_app.command("login")(login)
    auth_app.command("logout")(logout)


__all__ = ["register"]
