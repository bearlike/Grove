# grove.daemon — the loopback HTTP daemon (multi-repo, SSE)

> ↑ [root](../../../CLAUDE.md)

The FastAPI service that exposes the engine over HTTP. One process serves every repo via the [`RepoRegistry`](../core/CLAUDE.md); responses are [Views](../core/contracts/CLAUDE.md), never engine dataclasses. Loopback-only.

## Bind & auth

- **Bind `127.0.0.1` only; never widen it.** There is no `--host 0.0.0.0` blessing. The loopback constraint is *load-bearing*: it is precisely why endpoint auth is deferred — we trust the user's existing SSH credentials instead of designing token/mTLS. Remote access is via SSH port-forward, owned by the [client](../client/CLAUDE.md)'s `SshTransport` (one connection, HTTP forward + interactive attach). If you ever want network exposure, the answer is "no" inside Grove: stand up Tailscale, WireGuard, or an authenticated reverse proxy *outside* it.
- **Browser auth is cookie → BFF → bearer → daemon.** The webapp's server-side BFF holds the token and attaches the bearer; the daemon endpoints use the normal bearer dependency. The token never reaches the browser.

## Request/response shape

- **Request-body Pydantic models live at module scope, never in a closure.** We use `from __future__ import annotations` everywhere, so handler signatures are string forward-refs. Pydantic v2's `TypeAdapter` rebuild resolves those strings against the function's *globals* — it cannot see closure scope. A `class _Body(BaseModel)` defined inside `build_app()` raises `PydanticUserError: TypeAdapter[_Body] is not fully defined` on the first request. Put wire types at module top with a leading underscore: the underscore keeps the surface daemon-internal, module scope makes the symbol resolvable. Same trap for any inline `Annotated[Union, Field(discriminator=...)]`.
- **Error envelope is `{"detail": {"error": <code>, "message": <text>}}`.** `_grove_error_to_http`'s `code_map` translates each typed `GroveError` to an HTTP status + code. **Subclass entries MUST precede their parent** — the map is a linear `isinstance` scan, first match wins. `BranchError` is the catch-all for any new branch subclass we forget; `WorkspaceNotFound`/`WorkspaceStateError` map to 404/409; bare `GroveError` falls through to 500 / `grove_error`. Widen the map when a new typed subclass appears. Clients parse the envelope and raise `ProtocolError(code, message, status)`, preserving the typed error to the UI. **The error codes are a wire contract — keep them in sync with [contracts](../core/contracts/CLAUDE.md).** Mind the two session domains: `agent_session_not_found` (coding-agent transcripts, `AgentSessionNotFound`) vs the auth router's `session_not_found` (revoked bearer sessions) — the names collide easily, the domains never do.

## Session history endpoints

- **Session history is fetch-on-demand (`GET /workspaces/{id}/sessions` + `.../sessions/{sid}/turns`), never pushed over SSE.** Turns are unbounded; the stream stays small. Both endpoints run the transcript scan in the executor (like `/activity`) and are bounded to one workspace's cwd via `SessionExplorer.for_workspace`. `session_id` on the turns route must be the full id — prefix resolution stays a CLI affordance.

## Activity stream (SSE)

- **`GET /events` is a hand-rolled `StreamingResponse`, not `sse-starlette`.** Any new dependency needs `uv lock`, which re-resolves the graph and 404s on the dead `mkdocs-shadcn-mewbo` wheel. SSE framing (`id:` / `event:` / `data:`) is ~3 lines, so we emit `text/event-stream` frames with zero new deps. Documented in OpenAPI via `responses={200: {"model": DashboardEvent}}` so webapp codegen still picks up the envelope.
- **`_sse.py::_SseHub` is the sync → async bridge.** It subscribes once to the *synchronous* `ActivityService` bus; each `DashboardDelta` hops to the loop via `loop.call_soon_threadsafe`, then fans out to per-connection **bounded** `asyncio.Queue`s that **drop-oldest** on overflow — a wedged browser never back-pressures the engine (best-effort side effects isolate their failures). A shared ring buffer holds the last N events for `Last-Event-ID` replay; when the gap exceeds the buffer, send a fresh `snapshot` instead.
- **Poll runs in an executor.** The lifespan poll task runs `ActivityService.poll_once()` in an executor because it does blocking git/tmux I/O. That's why the service's `seq` is an `itertools.count` (atomic under the GIL) exposed via `next_seq()` — both the executor thread (deltas) and the loop thread (snapshots) stamp ids from it.
- **Do NOT add an `is_disconnected()` poll to the generator.** Starlette's `StreamingResponse` runs its own disconnect watcher that cancels the generator (the `finally: unsubscribe()` runs then). A manual poll only adds deadlock surface.
- **Test the infinite stream by driving the ASGI app directly.** Build the `scope`, feed `http.request`, capture the first `http.response.body`, return `http.disconnect`. The sync `TestClient` deadlocks on it and httpx's `ASGITransport` buffers the whole never-ending body.

## Session lessons

(Folded into the sections above. Add daemon-only HTTP/SSE/auth lessons here; engine facts go to [core](../core/CLAUDE.md), wire shapes to [contracts](../core/contracts/CLAUDE.md), transport to [client](../client/CLAUDE.md).)
