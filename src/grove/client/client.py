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
from grove.core.contracts.activity import DashboardSnapshotView
from grove.core.contracts.agents import AgentSummaryView
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.phase import PhaseView
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.sessions import SessionDetailView, SessionSummaryView, TodoListView
from grove.core.contracts.tickets import (
    TicketProviderName,
    TicketProviderView,
    TicketRef,
    TicketSelector,
)
from grove.core.contracts.views import (
    ATTACH_INSTRUCTION_ADAPTER,
    AttachInstructionView,
    HealthView,
    ProjectView,
    WhoamiView,
    WorkspacePeekView,
    WorkspaceStateView,
)
from grove.core.phase import TaskPhase

if TYPE_CHECKING:
    from grove.client.attach import AttachSession


class GroveClient:
    """One client per backend. Speaks the Grove daemon's REST protocol."""

    _DEFAULT_TIMEOUT_S = 30.0
    """Per-request budget for the ordinary read/steer calls — a daemon that has
    not answered a listing or a keystroke injection in 30s is wedged, not busy."""

    _LIFECYCLE_TIMEOUT_S = 1260.0
    """Per-request budget for create/resume/respawn, the calls that run the
    user's init script, provision the workspace's container, and launch an agent.

    It is deliberately larger than ``_DEFAULT_TIMEOUT_S`` rather than tuned to a
    guess: the engine bounds the init script alone at
    ``InitScriptConfig.timeout_seconds`` (default 300s), so a single 30s budget
    for the whole operation gave up an order of magnitude before the daemon's own
    limit on ONE of its steps. That is what made ``grove create`` succeed from the
    shell (in-process, no HTTP, no deadline) while the identical MCP/daemon call
    "failed" — and failed *after* the daemon had already committed to the work, so
    the create landed anyway and the caller was told nothing. A client
    deadline shorter than the server's own bound reports a healthy slow operation
    as a failure.

    The budget is the SUM of the engine's own per-step bounds plus headroom:
    ``init_script`` 300s + ``container.up_timeout_seconds`` 900s (a cold
    devcontainer build pulls a base image and installs Features) + 60s for
    `git worktree add` and the agent launch. A create that times out here is
    the worst failure shape for a containerized workspace — the daemon may
    finish and leave a container the caller has no record of — so the
    ordering is asserted in a test, not merely documented here.
    """

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
        # Deferred import, mirroring the AttachSession pattern in transport.py:
        # the symbol resolves at call time, not module-import time, so this
        # file has no import-order dependency on transport.py.
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

    async def list_workspaces(
        self,
        *,
        repo: Path | None = None,
        ticket_provider: TicketProviderName | None = None,
        ticket_id: str | None = None,
    ) -> list[WorkspaceStateView]:
        """Cross-repo (default) or single-repo (``repo``) workspace listing.

        ``ticket_provider``/``ticket_id`` narrow to the single workspace
        tracking that ticket (``WorkspaceManager.find_by_ticket`` on the
        daemon) — the issue-ops "does a workspace already exist for this
        ticket" lookup. Both must be given together (a ticket id alone is
        ambiguous across providers); the wire is a single ``ticket=<provider>:
        <id>`` query param.
        """
        if (ticket_provider is None) != (ticket_id is None):
            raise ValueError("ticket_provider and ticket_id must be given together")
        params: dict[str, str] = {}
        if repo is not None:
            params["repo"] = str(repo)
        if ticket_provider is not None and ticket_id is not None:
            params["ticket"] = f"{ticket_provider}:{ticket_id}"
        body = await self._get("/workspaces", params=params or None)
        return [WorkspaceStateView.model_validate(item) for item in body]

    async def create_workspace(self, req: CreateWorkspaceRequest) -> WorkspaceStateView:
        body = await self._post(
            "/workspaces",
            json_payload=req.model_dump(mode="json"),
            timeout=self._LIFECYCLE_TIMEOUT_S,
        )
        return WorkspaceStateView.model_validate(body)

    async def get_workspace(self, ws_id: str) -> WorkspaceStateView:
        body = await self._get(f"/workspaces/{ws_id}")
        return WorkspaceStateView.model_validate(body)

    async def pause(self, ws_id: str, *, force: bool = False) -> WorkspaceStateView:
        body = await self._post(f"/workspaces/{ws_id}/pause", json_payload={"force": force})
        return WorkspaceStateView.model_validate(body)

    async def resume(self, ws_id: str) -> WorkspaceStateView:
        body = await self._post(
            f"/workspaces/{ws_id}/resume",
            json_payload={},
            timeout=self._LIFECYCLE_TIMEOUT_S,
        )
        return WorkspaceStateView.model_validate(body)

    async def respawn(self, ws_id: str) -> WorkspaceStateView:
        body = await self._post(
            f"/workspaces/{ws_id}/respawn",
            json_payload={},
            timeout=self._LIFECYCLE_TIMEOUT_S,
        )
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

        Wraps ``POST /workspaces/{id}/message`` with body ``{"text": ...}``.
        Tolerates both 200 and 204 success shapes, so this method pins only
        the request contract. A daemon predating the endpoint answers 404/405
        with a non-envelope body; that surfaces as ``ProtocolError`` with
        ``code="http_error"``, which callers treat as capability-unavailable
        rather than failure (see ``grove.mcp``).

        Rides ``_request`` like every other verb rather than reaching for the
        httpx client directly — a bypass here is precisely how an unguarded
        httpx timeout could reach the MCP surface without translation.
        """
        resp = await self._request(
            "POST", f"/workspaces/{ws_id}/message", json_payload={"text": text}
        )
        if not resp.is_success:
            self._raise_for_status(resp)

    async def get_attach(self, ws_id: str) -> AttachInstructionView:
        body = await self._get(f"/workspaces/{ws_id}/attach")
        return ATTACH_INSTRUCTION_ADAPTER.validate_python(body)

    async def peek(self, ws_id: str) -> WorkspacePeekView:
        body = await self._get(f"/workspaces/{ws_id}/peek")
        return WorkspacePeekView.model_validate(body)

    async def get_activity(self) -> DashboardSnapshotView:
        """One cross-project snapshot of every workspace's live status.

        Mirrors ``GET /activity`` — zero-argument like :meth:`list_projects`,
        because it is the read a watcher makes when it holds nothing but wants
        the whole fleet. This is the ONLY read that answers the two axes the
        per-workspace routes cannot answer together at fleet scale: the blended
        ``AgentActivityView`` (working / waiting / ``needs_attention``, plus any
        live ``questions``) and each workspace's reported ``phase`` and todo
        counts. Every other client method here answers for ONE workspace, so a
        caller polling twenty of them paid twenty round trips to learn what this
        returns in one.

        A snapshot, not a subscription: the daemon's SSE ``/events`` stream is
        the push half and stays a transport concern (the TUI and webapp consume
        it directly). Callers that poll should mind the cost — ``snapshot()``
        does live git/tmux I/O per workspace daemon-side.
        """
        body = await self._get("/activity")
        return DashboardSnapshotView.model_validate(body)

    async def list_branches(
        self, *, repo: Path, scope: Literal["local", "remote"]
    ) -> list[BranchInfo]:
        body = await self._get("/branches", params={"repo": str(repo), "scope": scope})
        return [BranchInfo.model_validate(item) for item in body]

    async def list_projects(self) -> list[ProjectView]:
        """Every project this daemon serves, newest config union included.

        The call to make when you hold no repo path yet: a row's ``repo_root``
        is the argument :meth:`list_agents`, :meth:`list_branches`, and
        :meth:`create_workspace` all expect. Mirrors ``GET /projects``, which
        takes no parameters because it is the listing that precedes knowing a
        repo."""
        body = await self._get("/projects")
        return [ProjectView.model_validate(item) for item in body]

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

    async def list_sessions(
        self, *, repo: Path | None = None, limit: int = 50
    ) -> list[SessionSummaryView]:
        """Agent sessions newest-first — host-wide (default) or for one ``repo``.

        Mirrors ``GET /sessions``, where scope is a value of the same parameter:
        omitting ``repo`` returns the host catalog (every session in every
        adapter's store, including directories Grove has never managed), giving
        ``repo`` returns that project's fully-parsed listing. A catalog row is
        metadata-only, so its ``activity``/``size_bytes`` are ``None`` while its
        ``cwd``/``project``/``live`` are populated — see ``SessionSummaryView``.
        """
        params: dict[str, str] = {"limit": str(limit)}
        if repo is not None:
            params["repo"] = str(repo)
        body = await self._get("/sessions", params=params)
        return [SessionSummaryView.model_validate(item) for item in body]

    async def session_turns(
        self, session_id: str, *, kind: str, cwd: str, last: int | None = None
    ) -> SessionDetailView:
        """The conversation of a session that need not belong to any workspace.

        Mirrors ``GET /sessions/{id}/turns``. ``kind`` and ``cwd`` are the
        ``adapter_kind``/``cwd`` of the row this id came from — pass them back
        verbatim; they are how a session with no workspace is addressed at all.
        ``last`` keeps only the tail. A row with no ``cwd`` cannot be read.
        """
        params: dict[str, str] = {"kind": kind, "cwd": cwd}
        if last is not None:
            params["last"] = str(last)
        body = await self._get(f"/sessions/{session_id}/turns", params=params)
        return SessionDetailView.model_validate(body)

    async def remap_session(self, ws_id: str, session_ref: str) -> WorkspaceStateView:
        """Pin an existing agent session as the workspace's tracked primary.

        Wraps ``POST /workspaces/{id}/session``. ``session_ref`` is a session id
        or a unique id-prefix, resolved in the workspace's project scope by the
        daemon. Returns the updated workspace state.
        """
        body = await self._post(
            f"/workspaces/{ws_id}/session",
            json_payload={"session_ref": session_ref},
        )
        return WorkspaceStateView.model_validate(body)

    async def get_phase(self, ws_id: str) -> PhaseView | None:
        """The workspace's current task-phase claim.

        Wraps ``GET /workspaces/{id}/phase``. ``None`` means the agent has
        not reported a phase yet — a real answer, not an error; distinguish
        it from "reported scoping" rather than collapsing the two.
        """
        body = await self._get(f"/workspaces/{ws_id}/phase")
        return PhaseView.model_validate(body) if body is not None else None

    async def set_phase(self, ws_id: str, phase: TaskPhase, note: str | None = None) -> PhaseView:
        """Set or correct the workspace's task-phase claim from outside the agent.

        Wraps ``POST /workspaces/{id}/phase`` — the manual counterpart to the
        agent's own file-channel report (see ``grove.core.phase``), mirroring
        ``remap_session``'s trusted-write shape.
        """
        payload: dict[str, object] = {"phase": phase}
        if note is not None:
            payload["note"] = note
        body = await self._post(f"/workspaces/{ws_id}/phase", json_payload=payload)
        return PhaseView.model_validate(body)

    async def get_todo(self, ws_id: str) -> TodoListView:
        """The workspace's current todo/checklist state.

        Wraps ``GET /workspaces/{id}/todo``. Raises ``ProtocolError`` with
        code ``agent_session_not_found`` when the workspace has no recorded
        agent session; an empty ``TodoListView`` means a session exists but
        no todo/Task tool has been called yet — a real, not-yet-populated
        state, never conflated with the 404.
        """
        body = await self._get(f"/workspaces/{ws_id}/todo")
        return TodoListView.model_validate(body)

    async def attach_ticket(self, ws_id: str, selector: TicketSelector) -> WorkspaceStateView:
        """Associate a ticket with a workspace — returns the updated state."""
        body = await self._post(
            f"/workspaces/{ws_id}/tickets",
            json_payload=selector.model_dump(mode="json"),
        )
        return WorkspaceStateView.model_validate(body)

    async def attach_ticket_by_ref(self, ws_id: str, ref: str) -> WorkspaceStateView:
        """Associate a ticket with a workspace from a raw human-typed reference.

        Wraps the SAME ``POST /workspaces/{id}/tickets`` route as
        :meth:`attach_ticket`, sending ``{"ref": ...}`` instead of a resolved
        selector — a URL, ``#42``, ``42``, or ``owner/repo#42``. Resolution
        (provider + issue-vs-PR) happens server-side, so a caller with no
        provider name in hand never has to import or
        reimplement the link grammar. Raises ``ProtocolError`` with code
        ``ticket_link_ambiguous``/``ticket_link_invalid`` when ``ref`` names
        more than one enabled provider, or parses as nothing at all.
        """
        body = await self._post(f"/workspaces/{ws_id}/tickets", json_payload={"ref": ref})
        return WorkspaceStateView.model_validate(body)

    async def detach_ticket(
        self, ws_id: str, provider: TicketProviderName, ticket_id: str
    ) -> WorkspaceStateView:
        """Remove a ticket association from a workspace — returns the updated state."""
        body = await self._delete(f"/workspaces/{ws_id}/tickets/{provider}/{ticket_id}")
        return WorkspaceStateView.model_validate(body)

    async def detach_ticket_by_ref(self, ws_id: str, ref: str) -> WorkspaceStateView:
        """Remove a ticket association by raw human-typed reference.

        Wraps ``DELETE /workspaces/{id}/tickets?ref=...`` — the ref-resolving
        sibling of :meth:`detach_ticket`, resolved server-side the same way
        :meth:`attach_ticket_by_ref` is.
        """
        body = await self._delete(f"/workspaces/{ws_id}/tickets", params={"ref": ref})
        return WorkspaceStateView.model_validate(body)

    async def open_attach(self, instruction: AttachInstructionView) -> AttachSession:
        """Return an interactive AttachSession for what *instruction* names.

        Local backend → spawns a PTY running it. Remote backend → re-uses the
        SSH connection to run it. Takes the instruction rather than a session
        name because a containerized workspace is reached by exec'ing into its
        container, not by naming a session on this host — and
        ``attach_argv()`` is where each variant answers that, so nothing here
        branches on the runtime.
        """
        return await self._transport.open_attach(instruction.attach_argv())

    # ─── private HTTP helpers ────────────────────────────────────────────────

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise TransportError("GroveClient not connected — call connect() first")
        return self._http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json_payload: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """Issue one request, translating every transport failure into ``TransportError``.

        THE seam every verb helper funnels through, and the reason it exists: a
        raw ``httpx`` exception must never reach a caller. This module's contract
        is "transport failures raise ``TransportError``", and
        **``httpx.TimeoutException`` stringifies to the EMPTY STRING**
        (verified: ``str(httpx.ReadTimeout(...)) == ""`` on a real read timeout,
        because httpcore maps a bare ``TimeoutError()`` through and its message is
        the empty string). Any caller that renders ``str(exc)`` unguarded therefore
        reports a failure with no reason whatsoever — the MCP SDK does exactly that
        (``f"Error executing tool {name}: {e}"``), which turns a dropped steering
        message into an error with an empty body and leaves an orchestrator no
        strategy but a blind retry.

        A timeout is called out separately because it is the one failure that is
        **not** a statement about the outcome: the daemon may be mid-operation and
        may still commit it, so the honest advice is to check state rather than
        retry. Everything else names its httpx type, which is what distinguishes a
        refused connection from a dropped one.
        """
        client = self._ensure_http()
        budget = self._DEFAULT_TIMEOUT_S if timeout is None else timeout
        try:
            return await client.request(
                method, path, params=params, json=json_payload, timeout=budget
            )
        except httpx.TimeoutException as exc:
            raise TransportError(
                f"{method} {path} to daemon {client.base_url} timed out after {budget:g}s. "
                "The daemon may still be running the operation and may yet apply it — "
                "check the workspace's state before retrying, since a blind retry can "
                "duplicate the effect."
            ) from exc
        except httpx.HTTPError as exc:
            # `or type(exc).__name__` because several httpx errors also carry an
            # empty message; the type name is the last resort that is never blank.
            detail = str(exc) or type(exc).__name__
            raise TransportError(
                f"{method} {path} to daemon {client.base_url} failed: "
                f"{type(exc).__name__}: {detail}"
            ) from exc

    async def _get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        # Returns parsed JSON. Typed Any (not object) so callers can
        # dispatch via list/dict indexing without re-narrowing — the
        # Pydantic ``model_validate`` call at the next line is the
        # actual boundary that pins the shape.
        return self._unwrap(await self._request("GET", path, params=params))

    async def _post(
        self,
        path: str,
        *,
        json_payload: dict[str, object],
        expect_204: bool = False,
        timeout: float | None = None,
    ) -> Any:
        resp = await self._request("POST", path, json_payload=json_payload, timeout=timeout)
        if expect_204:
            if resp.status_code != 204:
                self._raise_for_status(resp)
            return None
        return self._unwrap(resp)

    async def _patch(self, path: str, *, json_payload: dict[str, object]) -> Any:
        return self._unwrap(await self._request("PATCH", path, json_payload=json_payload))

    async def _delete(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        return self._unwrap(await self._request("DELETE", path, params=params))

    def _unwrap(self, resp: httpx.Response) -> Any:
        """Parsed JSON on success, a typed error otherwise — never a raw `ValueError`.

        The success branch is the second half of `_request`'s guarantee:
        a 200 whose body is not JSON is a transport-level failure — a proxy's
        error page, a truncated response, something that is not the daemon
        answering — so it must never escape as a bare `json.JSONDecodeError`,
        which is neither `ProtocolError` (so the capability-degrade branches
        cannot see it) nor `TransportError` (so this module's documented
        contract would be false for it). An unguarded call renders as
        "Expecting value: line 1 column 1", which reads like a Grove bug
        rather than a broken hop.
        """
        if resp.is_success:
            try:
                return resp.json()
            except ValueError as exc:
                raise TransportError(
                    f"{resp.request.method} {resp.request.url.path} returned "
                    f"{resp.status_code} with a body that is not JSON "
                    f"({type(exc).__name__}) — something other than the Grove daemon "
                    "answered, or the response was truncated"
                ) from exc
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
