"""Exception hierarchy for grove.core. Clients catch GroveError; subclasses signal kind."""

from __future__ import annotations

from pathlib import Path


class GroveError(Exception):
    """Base for every error raised by grove.core. Clients catch this."""


class ConfigError(GroveError):
    """Configuration could not be loaded, parsed, or validated."""


class GitError(GroveError):
    """A git subprocess failed or produced unexpected output."""


class TmuxError(GroveError):
    """A tmux operation failed; covers both libtmux and `tmux` subprocess failures."""


class ProcessError(GroveError):
    """A detached local process could not be spawned (the headless runtime).

    The one exception ``grove.core.process`` raises — the ``OSError`` from a
    failed ``Popen`` (missing binary, OS refusal) is narrowed here so the launch
    boundary handles exactly one type, mirroring :class:`TmuxError` for the tmux
    side-effect surface.
    """


class EnvSourceError(GroveError):
    """A configured ``env_file`` / ``env_command`` could not be resolved.

    The one exception ``grove.core.env_source`` raises, for a missing file, an
    unreadable one, or a command that fails, times out, or cannot be parsed as an
    argv. Deliberately its OWN type rather than a container one: the seam serves
    several config sections, and each consumer narrows it into its own family at
    its own boundary (the container arm re-raises :class:`ContainerError` so the
    create rollback keeps one except arm; the tickets arm re-raises
    :class:`TicketProviderError`). A message never carries a resolved VALUE.
    """


class ContainerError(GroveError):
    """A container-runtime lifecycle op failed (build / up / stop / down / exec).

    The one exception type the container side-effect modules
    (``container_runtime``, ``container_infra``) raise — a failed ``docker``
    subprocess is narrowed to this so callers (the launch fork's transactional
    rollback) handle exactly one type, mirroring :class:`TmuxError`. Best-effort
    reads (``DockerCli.read``, ``status``) never raise; only mutating lifecycle
    ops do.
    """


class DevcontainerError(ContainerError):
    """A ``@devcontainers/cli`` invocation failed (read-configuration/build/up/exec).

    Subclasses :class:`ContainerError` so a caller that already handles "the
    container runtime failed" needs no new except arm; the subtype exists for
    ``container_id``. **A failed ``up`` can still have created a container** —
    the CLI reports ``outcome:"error"`` alongside a ``containerId`` — and losing
    that id leaks a container teardown can never find, so it is carried on the
    error rather than logged and dropped.
    """

    def __init__(self, message: str, *, container_id: str | None = None) -> None:
        super().__init__(message)
        self.container_id = container_id


class ContainerRequired(GroveError):
    """A host runtime was asked for in a project whose config forbids it.

    The refusal arm of the runtime decision tree: a committed
    ``customizations.grove.requires_container`` may RAISE the isolation floor
    (a capability *request*, always allowed), so honoring ``--runtime host``
    there would silently hand back the runtime the project declared unsafe.
    Raised before any side effect, like every other create-time validation.
    """


class WorkspaceNotFound(GroveError):
    """Lookup by id did not match any persisted workspace."""


class WorkspaceStateError(GroveError):
    """A lifecycle transition was requested from an incompatible status."""


class AgentSessionNotFound(GroveError):
    """No agent session with that id is recorded for the workspace.

    Distinct from the auth-domain :class:`SessionNotFound` below — this is
    about coding-agent transcripts (``SessionExplorer``), not bearer sessions.
    """


class MewboError(GroveError):
    """A Mewbo REST API call failed (transport, auth, or malformed response).

    The one exception type ``grove.core.mewbo.MewboClient`` raises — httpx
    exceptions never leak past that boundary, so callers (the launch fork,
    the adapter's best-effort reads, the steering arms) handle exactly one type.
    """


# ─── ticket provider errors (raised by grove.core.tickets) ──────────────────


class TicketProviderError(GroveError):
    """A ticket-tracker API call failed (transport, auth, or malformed response).

    The one exception type the ``HttpTicketProvider`` subclasses raise — httpx
    exceptions never leak past that boundary, mirroring :class:`MewboError`. Maps
    to a 502 at the daemon edge (the failure is upstream, not the client's).
    """


class TicketProviderNotConfigured(GroveError):
    """A provider was named (attach / create-from-ticket / get) but isn't enabled.

    Distinct from :class:`TicketProviderError`: nothing went wrong on the wire —
    the requested tracker simply isn't turned on for this repo. Maps to 404.
    """


class TicketCommentsUnsupported(GroveError):
    """A provider was asked for comment-thread I/O it has no implementation for.

    Capability-based like :class:`SteeringUnsupported` below, not state-based —
    retrying cannot succeed regardless of auth or upstream state. Only Gitea and
    GitHub back the issue-ops comment loop (list/post/edit/react); Linear's
    grammar stays branch-association-only, and inherits this raise from
    ``HttpTicketProvider``'s base default rather than every non-implementing
    provider needing its own stub. Distinct from :class:`TicketProviderNotConfigured`
    (right provider, no credential) and :class:`TicketProviderError` (right
    provider, wire failure) — this one means the tracker itself has no such
    capability in Grove.
    """


class TicketPullRequestsUnsupported(GroveError):
    """A provider was asked for pull-request metadata it has no concept of.

    The capability sibling of :class:`TicketCommentsUnsupported`, separate
    because the capabilities are separate: a tracker can back the comment loop
    and still have no pull requests. Gitea and GitHub answer from their ``pulls``
    namespace; Linear inherits ``HttpTicketProvider``'s base-default raise.
    """


class TicketAssigneesUnsupported(GroveError):
    """A provider was asked to change assignees it has no concept of.

    The third capability sibling of :class:`TicketCommentsUnsupported`, and
    separate for the same reason the pull-request one is: a tracker can back the
    comment loop and still expose no assignee write. Deliberately NOT folded into
    the comment capability even though the same two forges back both — assigning
    needs repo *write*, a strictly stronger grant than commenting, so a
    deployment can honestly have one and not the other and must be able to tell
    them apart.
    """


class TicketLinkError(GroveError):
    """Human-supplied text could not be turned into a ticket reference.

    Raised by the link parser (a browser URL, ``#42``, ``42``, ``owner/repo#42``)
    when the text matches no recognized form, or when no *enabled* provider can
    own the reference it named. The input is the thing at fault, so a caller
    renders the message verbatim rather than retrying.
    """


class TicketLinkAmbiguous(TicketLinkError):
    """A parsed link could belong to more than one enabled provider.

    A bare ``#42`` with both numeric trackers enabled names two different
    tickets. Ambiguity is an explicit typed outcome — never a silent pick of the
    first provider — so the caller re-asks with a qualified form (a full URL or
    ``owner/repo#42``). The message names every candidate provider.
    """


# ─── notification errors (raised by grove.core.notifications) ───────────────


class NotificationError(GroveError):
    """A notification channel failed to deliver (transport, auth, bad response).

    Channels (``GotifyNotificationChannel``, ``WebhookNotificationChannel``)
    narrow every httpx failure to a subclass of this; the broker's best-effort
    guard catches it, logs a structured per-channel outcome, and never re-raises
    into the activity path. Subclassed per channel so logs name the sink.
    """


class GotifyError(NotificationError):
    """A Gotify ``POST /message`` failed."""


class WebhookError(NotificationError):
    """A generic-webhook ``POST`` failed."""


# ─── steering errors (raised by WorkspaceManager.send_message / interrupt) ──


class PaneNotFound(GroveError):
    """The workspace is live but no tmux pane resolved to steer.

    ``pane_target`` returned None — the session is up yet reports no
    windows (emptied or reorganized externally). Distinct from
    :class:`WorkspaceStateError`: the lifecycle status permitted the
    operation; the live session just has nowhere to type.
    """


class SteeringUnsupported(GroveError):
    """The workspace's agent kind has no implementation for this steering op.

    Capability-based, not state-based — retrying after a lifecycle change
    cannot succeed. Raised for interrupt on tmux-hosted agents (no safe
    generic interrupt exists).
    """


class CapabilityUnavailable(GroveError):
    """The workspace's runtime cannot perform this operation at all.

    Capability-based like :class:`SteeringUnsupported`, but keyed on the launch
    backend rather than the agent kind: a headless workspace (``provides_pane``
    False) has no tmux pane, so pane-bound ops — ``send_message`` / ``interrupt``
    / a pane snapshot — have nowhere to act. Distinct from :class:`PaneNotFound`
    (a live session that momentarily reports no window): here there is
    deliberately no pane and no lifecycle change can produce one. A headless
    runtime gains real steering only through the native input channel
    (stream-json).
    """


# ─── live-question answering (raised by WorkspaceManager.answer_question) ────


class QuestionNotPending(GroveError):
    """No pending question matches the answer request.

    Either the session's sidecar carries no captured question, or its
    ``tool_use_id`` no longer matches the one being answered — the human
    already resolved it in the terminal, or a newer question superseded it.
    State-shaped (maps to 409): the request was well-formed, the live target
    just moved on. The message names which case.
    """


class QuestionAnswerInvalid(GroveError):
    """The answer plan doesn't fit the captured question payload.

    Raised when the per-question kind rules fail against the *captured*
    questions (wrong number of answers, an out-of-range option index, a
    free-text answer on a multiSelect, an unsupported question kind). Maps to
    422 — the plan itself is malformed for these questions, not a state
    conflict. Structural shape (exactly one of indexes/text, non-blank text)
    is caught earlier by the wire model's own validation.
    """


# ─── resume validation (raised by WorkspaceManager.create) ──────────────────


class ResumeNotSupported(GroveError):
    """A create named ``resume_session_id`` for an agent kind that can't resume
    an existing session by explicit id.

    Only filesystem CLI kinds carry a resume handle — ``claude_code``
    (``claude --resume <id>``) and ``codex`` (``codex resume <id>``). A remote
    (mewbo) session or a generic/shell agent has no such mechanism, so naming a
    resume id for one is a malformed request, not a runtime failure. Raised at
    create()'s validation gate before any side effect. Maps to 422 at the daemon
    edge — the request is well-formed but semantically invalid for this agent.
    """


# ─── onboarding errors (raised by grove.core.agents.onboarding) ─────────────


class OnboardError(GroveError):
    """A `claude mcp add` / `codex mcp add` subprocess exited non-zero.

    The one exception type ``register_mcp`` raises; the tool's own stderr is
    folded into the message so ``grove mcp install`` surfaces the real reason
    (already registered, binary not found, malformed args) without a second
    lookup.
    """


# ─── branch validation errors (raised by WorkspaceManager.create) ───────────


class BranchError(GroveError):
    """A `BranchPlan` could not be reconciled with live git state.

    Subclasses pinpoint the specific failure so clients (TUI flash today,
    HTTP 422 in a future API server) can render a tailored message.
    Always raised *before* any worktree side effect, so create-failure
    cleanup is unnecessary on this path.
    """


class BranchNotFound(BranchError):
    """A branch (local or remote) referenced by the plan does not exist."""


class BranchConflict(BranchError):
    """A new branch name in the plan collides with an existing branch."""


# ─── auth / pairing errors (raised by SessionStore + daemon dep) ───────────


class AuthError(GroveError):
    """Base for every authentication / pairing failure.

    Subclasses pinpoint the failure mode so the daemon can emit the right
    HTTP code + envelope. Always raised before any state-changing side
    effect — callers can treat the store as untouched on AuthError.
    """


class AuthInvalidToken(AuthError):
    """Bearer token missing, malformed, or not recognized by the store."""


class PairingNotFound(AuthError):
    """No challenge with that id (or it expired and was garbage-collected)."""


class PairingExpired(AuthError):
    """Challenge passed its TTL before approval / consumption."""


class PairingAlreadyResolved(AuthError):
    """Challenge already approved / denied / consumed; cannot transition again."""


class AuthRateLimited(AuthError):
    """Too many recent pair_init / pair_poll calls from the same source."""


class SessionNotFound(AuthError):
    """No session with that id — already revoked or never existed."""


class AuthStoreUnreadable(AuthError):
    """`auth.json` exists but cannot be used: corrupt, unreadable, or a newer version.

    A SERVER-side fault, not a caller's input, and it needs its own type
    because the two are otherwise indistinguishable: mapping every bare
    `GroveError` to `422 invalid_label` at `POST /auth/pair` tells a user with
    a perfectly good label that their label was invalid when the real cause is
    an interrupted write. Carries no path — that goes to the log, because this
    endpoint is unauthenticated by design.
    """


class BranchAlreadyCheckedOut(BranchError):
    """The requested existing-local branch is already checked out at another worktree.

    Carries structured context (`name`, `worktree`) so clients can render
    a useful message ("checked out at /path/to/wt") and a future API
    response can include them in the JSON payload.
    """

    def __init__(self, name: str, worktree: Path) -> None:
        super().__init__(f"branch {name!r} is already checked out at {worktree}")
        self.name = name
        self.worktree = worktree
