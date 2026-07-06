"""GroveClient — single class per backend. HTTP + attach factory.

Every method is a thin wrapper around an HTTP call against
``{transport.http_url}/...``. Engine errors come back as typed
``ProtocolError`` instances; transport failures as ``TransportError``.

Lifecycle::

    async with GroveClient(BackendConfig(...)) as client:
        ...

or explicit::

    client = GroveClient(cfg)
    await client.connect()
    ...
    await client.close()
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal

import httpx

from grove.client.backend import BackendConfig
from grove.client.errors import NeedsPairingError, ProtocolError, TransportError
from grove.client.transport import LocalTransport, Transport, UrlTransport
from grove.core.contracts.agents import AgentSummaryView
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import (
    TicketProviderName,
    TicketProviderView,
    TicketRef,
    TicketSelector,
)
from grove.core.contracts.views import (
    AttachInstructionView,
    HealthView,
    WhoamiView,
    WorkspacePeekView,
    WorkspaceStateView,
)

if TYPE_CHECKING:
    from grove.client.attach import AttachSession


class GroveClient:
    """One client per backend. Speaks the Grove daemon's REST protocol."""

    _DEFAULT_TIMEOUT_S = 30.0

    def __init__(self, config: BackendConfig) -> None:
        self._config = config
        self._transport: Transport = self._make_transport(config)
        self._http: httpx.AsyncClient | None = None

    @staticmethod
    def _make_transport(config: BackendConfig) -> Transport:
        if config.daemon_url is not None:
            if config.ssh_target is not None:
                raise ValueError("BackendConfig.daemon_url and ssh_target are mutually exclusive")
            return UrlTransport(config)
        if config.ssh_target is None:
            return LocalTransport(config)
        # SshTransport lands in Task 13; deferred import keeps Task 12 buildable.
        # Mirrors the AttachSession pattern in transport.py — symbol resolved
        # at call time, not module-import time, so this file builds cleanly
        # while the SSH transport is still in flight.
        from grove.client.transport import (  # type: ignore[attr-defined,unused-ignore] # noqa: PLC0415
            SshTransport,
        )

        transport: Transport = SshTransport(config)
        return transport

    async def __aenter__(self) -> GroveClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def connect(self) -> None:
        await self._transport.start()
        token = self._resolve_token()
        headers: dict[str, str] = {}
        if token is not None:
            headers["authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(
            base_url=self._transport.http_url,
            timeout=self._DEFAULT_TIMEOUT_S,
            headers=headers,
        )

    def _resolve_token(self) -> str | None:
        """Return the bearer token to attach to every request, or raise.

        An explicit ``BackendConfig.daemon_token`` always wins — it is the
        caller saying "use this credential" (``grove-mcp`` populates it
        from ``GROVE_API_TOKEN``), and skipping the mint below is what lets
        a URL backend reach a daemon on another machine.

        Local backend (no ``ssh_target``): mints a fresh session against the
        shared ``auth.json`` file. Daemon and client run as the same UID and
        both read the same file, so this works even though the daemon is in
        a child process — the file is the rendezvous, not the in-memory state.

        Remote backend: requires ``BackendConfig.daemon_token`` to be set
        (the client's first-connect pairing flow populates it). Raises
        ``NeedsPairingError`` if absent so the client can surface a pair
        modal — same code path as a 401 from the daemon (token revoked).
        """
        if self._config.daemon_token is not None:
            return self._config.daemon_token
        if self._config.ssh_target is None:
            from grove.core.auth import SessionStore  # noqa: PLC0415

            store = SessionStore()
            label = f"local-{self._config.label}"
            challenge = store.pair_init(label=label)
            store.pair_approve(challenge.challenge_id)
            _, token = store.pair_poll(challenge.challenge_id)
            return token
        raise NeedsPairingError(
            self._config.label,
            daemon_http_url=self._transport.http_url,
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        await self._transport.close()

    # ─── HTTP methods ────────────────────────────────────────────────────────

    async def health(self) -> HealthView:
        """Public liveness probe — minimal status + version. No auth needed
        on the daemon side, but the Python client still rides through its
        connected ``httpx.AsyncClient`` for transport consistency."""
        body = await self._get("/healthz")
        return HealthView.model_validate(body)

    async def whoami(self) -> WhoamiView:
        """Authenticated daemon identity + uptime — host, user, version,
        started_at, uptime_seconds, platform, python_version."""
        body = await self._get("/whoami")
        return WhoamiView.model_validate(body)

    async def list_workspaces(self) -> list[WorkspaceStateView]:
        body = await self._get("/workspaces")
        return [WorkspaceStateView.model_validate(item) for item in body]

    async def create_workspace(self, req: CreateWorkspaceRequest) -> WorkspaceStateView:
        body = await self._post("/workspaces", json_payload=req.model_dump(mode="json"))
        return WorkspaceStateView.model_validate(body)

    async def get_workspace(self, ws_id: str) -> WorkspaceStateView:
        body = await self._get(f"/workspaces/{ws_id}")
        return WorkspaceStateView.model_validate(body)

    async def pause(self, ws_id: str, *, force: bool = False) -> WorkspaceStateView:
        body = await self._post(f"/workspaces/{ws_id}/pause", json_payload={"force": force})
        return WorkspaceStateView.model_validate(body)

    async def resume(self, ws_id: str) -> WorkspaceStateView:
        body = await self._post(f"/workspaces/{ws_id}/resume", json_payload={})
        return WorkspaceStateView.model_validate(body)

    async def respawn(self, ws_id: str) -> WorkspaceStateView:
        body = await self._post(f"/workspaces/{ws_id}/respawn", json_payload={})
        return WorkspaceStateView.model_validate(body)

    async def interrupt(self, ws_id: str) -> None:
        """Interrupt the workspace's agent, where its adapter supports it.

        Sibling of ``send_message`` on the daemon's steer surface
        (``POST /workspaces/{id}/interrupt``, empty 204). A capability
        refusal surfaces as ``ProtocolError`` with code
        ``steering_unsupported`` (501).
        """
        await self._post(f"/workspaces/{ws_id}/interrupt", json_payload={}, expect_204=True)

    async def kill(self, ws_id: str, *, delete_branch: bool | None = None) -> None:
        await self._post(
            f"/workspaces/{ws_id}/kill",
            json_payload={"delete_branch": delete_branch},
            expect_204=True,
        )

    async def update_workspace(
        self,
        ws_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
    ) -> WorkspaceStateView:
        """Partial metadata update — title and/or description.

        ``title=None`` (default) leaves the title unchanged; pass a
        non-empty string to rename. ``description=None`` (default)
        leaves the description unchanged; pass ``""`` to clear it; pass
        a non-empty string to set it. At least one must be provided —
        the daemon refuses an empty body with a 422.
        """
        payload: dict[str, object] = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        body = await self._patch(f"/workspaces/{ws_id}", json_payload=payload)
        return WorkspaceStateView.model_validate(body)

    async def send_message(self, ws_id: str, text: str) -> None:
        """Inject a steering message into the workspace agent's pane.

        Wraps ``POST /workspaces/{id}/message`` with body ``{"text": ...}``
        (daemon issue #37). Tolerates both 200 and 204 success shapes — the
        endpoint is being built in parallel, so this method pins only the
        request contract. A daemon predating the endpoint answers 404/405
        with a non-envelope body; that surfaces as ``ProtocolError`` with
        ``code="http_error"``, which callers treat as capability-unavailable
        rather than failure (see ``grove.mcp``).
        """
        resp = await self._ensure_http().post(f"/workspaces/{ws_id}/message", json={"text": text})
        if not resp.is_success:
            self._raise_for_status(resp)

    async def get_attach(self, ws_id: str) -> AttachInstructionView:
        body = await self._get(f"/workspaces/{ws_id}/attach")
        return AttachInstructionView.model_validate(body)

    async def peek(self, ws_id: str) -> WorkspacePeekView:
        body = await self._get(f"/workspaces/{ws_id}/peek")
        return WorkspacePeekView.model_validate(body)

    async def list_branches(
        self, *, repo: Path, scope: Literal["local", "remote"]
    ) -> list[BranchInfo]:
        body = await self._get("/branches", params={"repo": str(repo), "scope": scope})
        return [BranchInfo.model_validate(item) for item in body]

    async def list_agents(self, repo: Path) -> list[AgentSummaryView]:
        """The agents a client may offer for ``repo`` — one row per merged
        ``cfg.agents`` entry, each carrying its per-agent model catalog
        (``models``, ≤10) for a create-form picker. Mirrors ``GET /agents``;
        read-only and non-git, so an arbitrary path yields the default cascade
        rather than an error."""
        body = await self._get("/agents", params={"repo": str(repo)})
        return [AgentSummaryView.model_validate(item) for item in body]

    # ─── tickets ─────────────────────────────────────────────────────────────

    async def list_ticket_providers(self, repo: Path) -> list[TicketProviderView]:
        """The trackers a client may offer for ``repo`` — one row per provider,
        each carrying its ``configured`` (credentials-present) signal so the UI
        grays out a dead picker rather than offering it."""
        body = await self._get("/tickets/providers", params={"repo": str(repo)})
        return [TicketProviderView.model_validate(item) for item in body]

    async def list_assigned_tickets(
        self,
        repo: Path,
        *,
        provider: TicketProviderName | None = None,
        status: str | None = None,
    ) -> list[TicketRef]:
        """Tickets assigned to the authenticated user across ``repo``'s trackers.

        ``provider`` narrows to a single tracker; ``status`` filters by the
        provider's status string. Both are omitted from the query when ``None``
        so the daemon applies its own default scope.
        """
        params: dict[str, str] = {"repo": str(repo)}
        if provider is not None:
            params["provider"] = provider
        if status is not None:
            params["status"] = status
        body = await self._get("/tickets/assigned", params=params)
        return [TicketRef.model_validate(item) for item in body]

    async def get_ticket(
        self, repo: Path, provider: TicketProviderName, ticket_id: str
    ) -> TicketRef:
        """Fetch one ticket by provider + canonical id, scoped to ``repo``."""
        body = await self._get(f"/tickets/{provider}/{ticket_id}", params={"repo": str(repo)})
        return TicketRef.model_validate(body)

    async def remap_session(self, ws_id: str, session_ref: str) -> WorkspaceStateView:
        """Pin an existing agent session as the workspace's tracked primary (#120).

        Wraps ``POST /workspaces/{id}/session``. ``session_ref`` is a session id
        or a unique id-prefix, resolved in the workspace's project scope by the
        daemon. Returns the updated workspace state.
        """
        body = await self._post(
            f"/workspaces/{ws_id}/session",
            json_payload={"session_ref": session_ref},
        )
        return WorkspaceStateView.model_validate(body)

    async def attach_ticket(self, ws_id: str, selector: TicketSelector) -> WorkspaceStateView:
        """Associate a ticket with a workspace — returns the updated state."""
        body = await self._post(
            f"/workspaces/{ws_id}/tickets",
            json_payload=selector.model_dump(mode="json"),
        )
        return WorkspaceStateView.model_validate(body)

    async def detach_ticket(
        self, ws_id: str, provider: TicketProviderName, ticket_id: str
    ) -> WorkspaceStateView:
        """Remove a ticket association from a workspace — returns the updated state."""
        body = await self._delete(f"/workspaces/{ws_id}/tickets/{provider}/{ticket_id}")
        return WorkspaceStateView.model_validate(body)

    async def open_attach(self, tmux_session: str) -> AttachSession:
        """Return an interactive AttachSession bound to the tmux session.

        Local backend → spawns a PTY running ``tmux attach``.
        Remote backend → re-uses the SSH connection to run ``tmux attach``.
        """
        return await self._transport.open_attach(tmux_session)

    # ─── private HTTP helpers ────────────────────────────────────────────────

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise TransportError("GroveClient not connected — call connect() first")
        return self._http

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        # Returns parsed JSON. Typed Any (not object) so callers can
        # dispatch via list/dict indexing without re-narrowing — the
        # Pydantic ``model_validate`` call at the next line is the
        # actual boundary that pins the shape.
        resp = await self._ensure_http().get(path, params=params)
        return self._unwrap(resp)

    async def _post(
        self,
        path: str,
        *,
        json_payload: dict[str, object],
        expect_204: bool = False,
    ) -> Any:
        resp = await self._ensure_http().post(path, json=json_payload)
        if expect_204:
            if resp.status_code != 204:
                self._raise_for_status(resp)
            return None
        return self._unwrap(resp)

    async def _patch(self, path: str, *, json_payload: dict[str, object]) -> Any:
        resp = await self._ensure_http().patch(path, json=json_payload)
        return self._unwrap(resp)

    async def _delete(self, path: str) -> Any:
        resp = await self._ensure_http().delete(path)
        return self._unwrap(resp)

    def _unwrap(self, resp: httpx.Response) -> Any:
        if resp.is_success:
            return resp.json()
        self._raise_for_status(resp)
        raise AssertionError("unreachable — _raise_for_status always raises")

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        try:
            body = resp.json()
            detail = body.get("detail", body) if isinstance(body, dict) else body
            if isinstance(detail, dict) and "error" in detail:
                raise ProtocolError(
                    code=str(detail["error"]),
                    message=str(detail.get("message", "")),
                    status=resp.status_code,
                )
        except ValueError:
            pass  # not JSON — fall through
        raise ProtocolError(code="http_error", message=resp.text, status=resp.status_code)
