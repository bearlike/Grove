"""IssueOpsEngine routing — the full create/steer/verb/drop/refuse matrix (#196).

In-memory fakes only (no git/tmux/network): a fake registry hands the engine a
fake Manager holding a REAL ``GroveConfig`` (so trigger/agent/permission/template
policy resolve exactly as in production) plus capturing verb methods, and a
capturing fake ticket provider. Each test asserts both the returned
``IssueOpsOutcome`` and the side effect the engine drove (which manager verb, or
which reply).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.issueops import IssueOpsEvent
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketComment
from grove.core.errors import TicketProviderError, WorkspaceStateError
from grove.core.issueops import SIGNATURE_MARKER, IssueOpsEngine
from grove.core.workspace import WorkspaceStatus

# ─── fakes ───────────────────────────────────────────────────────────────────


@dataclass
class _FakeMatch:
    """Stands in for the reconciled WorkspaceState ``find_by_ticket`` returns."""

    id: str
    status: WorkspaceStatus


class _FakeProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.comments: list[tuple[str, str]] = []

    def post_comment(self, ticket_id: str, body: str) -> TicketComment:
        if self.fail:
            raise TicketProviderError("wire down")
        self.comments.append((ticket_id, body))
        return TicketComment(id="reply-1", body=body)


class _FakeProviders:
    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider

    def get(self, name: str) -> _FakeProvider:
        del name
        return self._provider


class _FakeManager:
    def __init__(self, cfg: GroveConfig, *, match: _FakeMatch | None = None) -> None:
        self._cfg = cfg
        self.match = match
        self.provider = _FakeProvider()
        self.calls: list[tuple[str, Any, ...]] = []
        self.create_error: Exception | None = None
        self.pause_error: Exception | None = None
        self.send_error: Exception | None = None
        self.created = _FakeMatch("ws-created", WorkspaceStatus.RUNNING)

    @property
    def config(self) -> GroveConfig:
        return self._cfg

    @property
    def repo_root(self) -> Path:
        return Path("/repo")

    @property
    def ticket_providers(self) -> _FakeProviders:
        return _FakeProviders(self.provider)

    def find_by_ticket(self, provider: str, ticket_id: str) -> _FakeMatch | None:
        self.calls.append(("find_by_ticket", provider, ticket_id))
        return self.match

    def create(self, request: CreateWorkspaceRequest) -> _FakeMatch:
        self.calls.append(("create", request))
        if self.create_error is not None:
            raise self.create_error
        return self.created

    def send_message(self, ws_id: str, text: str) -> None:
        self.calls.append(("send_message", ws_id, text))
        if self.send_error is not None:
            raise self.send_error

    def pause(self, ws_id: str) -> None:
        self.calls.append(("pause", ws_id))
        if self.pause_error is not None:
            raise self.pause_error

    def resume(self, ws_id: str) -> None:
        self.calls.append(("resume", ws_id))

    def kill(self, ws_id: str) -> None:
        self.calls.append(("kill", ws_id))


class _FakeRegistry:
    def __init__(self, manager: _FakeManager) -> None:
        self._manager = manager

    def known_roots(self) -> list[Path]:
        return [Path("/repo")]

    def get(self, root: Path) -> _FakeManager:
        del root
        return self._manager


@dataclass
class _FakePublisher:
    calls: list[tuple[IssueOpsEvent, Any]] = field(default_factory=list)

    def publish(self, event: IssueOpsEvent, manager: Any) -> None:
        self.calls.append((event, manager))


# ─── builders ──────────────────────────────────────────────────────────────


def _config(**issueops: Any) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "tickets": {"gitea": {"enabled": True, "owner": "acme", "repo": "widget"}},
            "issueops": issueops,
        }
    )


def _event(**over: Any) -> IssueOpsEvent:
    base: dict[str, Any] = {
        "provider": "gitea",
        "owner": "acme",
        "repo": "widget",
        "issue_number": 42,
        "issue_title": "Fix the flaky login",
        "issue_body": "It fails one run in ten.",
        "issue_url": "https://git.example/acme/widget/issues/42",
        "comment_id": "comment-100",
        "comment_body": "@grove fix it",
        "actor": "alice",
        "actor_permission": "write",
        "actor_is_bot": False,
    }
    base.update(over)
    return IssueOpsEvent(**base)


def _engine(manager: _FakeManager, publisher: Any = None) -> IssueOpsEngine:
    return IssueOpsEngine(registry=_FakeRegistry(manager), status_publisher=publisher)  # type: ignore[arg-type]


def _last_reply(manager: _FakeManager) -> str:
    assert manager.provider.comments, "expected a reply comment to have been posted"
    return manager.provider.comments[-1][1]


# ─── free-text prompt: create vs steer ───────────────────────────────────────


def test_prompt_with_no_existing_workspace_creates_one() -> None:
    mgr = _FakeManager(_config())
    outcome = _engine(mgr).handle(_event(comment_body="@grove please fix the login bug"))
    assert (outcome.action, outcome.workspace_id) == ("created", "ws-created")
    created = [c for c in mgr.calls if c[0] == "create"]
    assert len(created) == 1
    request = created[0][1]
    assert isinstance(request, CreateWorkspaceRequest)
    assert request.agent_name == "claude"  # the IssueOpsConfig default
    assert request.ticket is not None
    assert (request.ticket.provider, request.ticket.id) == ("gitea", "42")
    # the rendered prompt weaves in the issue + the command text
    assert request.initial_prompt is not None
    assert "Fix the flaky login" in request.initial_prompt
    assert "please fix the login bug" in request.initial_prompt


def test_prompt_with_a_running_workspace_steers_it() -> None:
    mgr = _FakeManager(_config(), match=_FakeMatch("ws-7", WorkspaceStatus.ACTIVE))
    outcome = _engine(mgr).handle(_event(comment_body="@grove also handle logout"))
    assert (outcome.action, outcome.workspace_id) == ("steered", "ws-7")
    assert ("send_message", "ws-7", "also handle logout") in mgr.calls
    assert not mgr.provider.comments  # a steer is silent, not a reply


def test_prompt_with_a_paused_workspace_refuses_and_replies() -> None:
    mgr = _FakeManager(_config(), match=_FakeMatch("ws-7", WorkspaceStatus.PAUSED))
    outcome = _engine(mgr).handle(_event(comment_body="@grove keep going"))
    assert (outcome.action, outcome.code, outcome.workspace_id) == (
        "refused",
        "workspace_not_running",
        "ws-7",
    )
    assert "resume" in _last_reply(mgr)
    assert ("send_message",) not in [(c[0],) for c in mgr.calls]


def test_create_failure_is_reported_and_replied() -> None:
    mgr = _FakeManager(_config())
    mgr.create_error = WorkspaceStateError("branch already exists")
    outcome = _engine(mgr).handle(_event(comment_body="@grove start work"))
    assert (outcome.action, outcome.code) == ("refused", "create_failed")
    assert "branch already exists" in _last_reply(mgr)


# ─── verbs ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("verb", "call", "action"),
    [
        ("pause", "pause", "paused"),
        ("resume", "resume", "resumed"),
        ("stop", "kill", "stopped"),
    ],
)
def test_lifecycle_verb_drives_the_matching_manager_op(verb: str, call: str, action: str) -> None:
    mgr = _FakeManager(_config(), match=_FakeMatch("ws-3", WorkspaceStatus.IDLE))
    outcome = _engine(mgr).handle(_event(comment_body=f"@grove {verb}"))
    assert (outcome.action, outcome.workspace_id) == (action, "ws-3")
    assert (call, "ws-3") in mgr.calls


def test_verb_without_a_workspace_refuses_and_replies() -> None:
    mgr = _FakeManager(_config(), match=None)
    outcome = _engine(mgr).handle(_event(comment_body="@grove pause"))
    assert (outcome.action, outcome.code) == ("refused", "no_workspace")
    assert _last_reply(mgr)
    assert not any(c[0] == "pause" for c in mgr.calls)


def test_verb_lifecycle_failure_is_reported_and_replied() -> None:
    mgr = _FakeManager(_config(), match=_FakeMatch("ws-3", WorkspaceStatus.IDLE))
    mgr.pause_error = WorkspaceStateError("already paused")
    outcome = _engine(mgr).handle(_event(comment_body="@grove pause"))
    assert (outcome.action, outcome.code, outcome.workspace_id) == (
        "refused",
        "pause_failed",
        "ws-3",
    )
    assert "already paused" in _last_reply(mgr)


def test_status_verb_triggers_the_publisher_seam() -> None:
    mgr = _FakeManager(_config())
    publisher = _FakePublisher()
    outcome = _engine(mgr, publisher).handle(_event(comment_body="@grove status"))
    assert outcome.action == "status"
    assert len(publisher.calls) == 1
    assert publisher.calls[0][0].comment_id == "comment-100"


def test_status_verb_works_with_the_default_noop_publisher() -> None:
    mgr = _FakeManager(_config())
    outcome = _engine(mgr).handle(_event(comment_body="@grove status"))
    assert outcome.action == "status"
    assert not mgr.provider.comments  # no reply for a status


# ─── usage / drops ────────────────────────────────────────────────────────────


def test_usage_command_refuses_with_a_reply() -> None:
    mgr = _FakeManager(_config())
    outcome = _engine(mgr).handle(_event(comment_body="@grove stop everything now"))
    assert (outcome.action, outcome.code) == ("refused", "usage")
    assert _last_reply(mgr)  # never silence a bad command
    assert not any(c[0] == "kill" for c in mgr.calls)


def test_untriggered_comment_is_ignored() -> None:
    mgr = _FakeManager(_config())
    outcome = _engine(mgr).handle(_event(comment_body="just a normal human comment"))
    assert (outcome.action, outcome.code) == ("ignored", "not_triggered")
    assert not mgr.calls


def test_bot_actor_is_ignored() -> None:
    mgr = _FakeManager(_config())
    outcome = _engine(mgr).handle(_event(actor_is_bot=True))
    assert (outcome.action, outcome.code) == ("ignored", "bot")
    assert not mgr.calls


def test_own_signature_marker_is_ignored() -> None:
    mgr = _FakeManager(_config())
    body = f"@grove fix it {SIGNATURE_MARKER}"
    outcome = _engine(mgr).handle(_event(comment_body=body))
    assert (outcome.action, outcome.code) == ("ignored", "signature_marker")
    assert not mgr.calls


def test_duplicate_comment_is_ignored_on_retry() -> None:
    mgr = _FakeManager(_config())
    engine = _engine(mgr)
    event = _event(comment_body="@grove start work")
    first = engine.handle(event)
    second = engine.handle(event)  # CI redelivers the identical event
    assert first.action == "created"
    assert (second.action, second.code) == ("ignored", "duplicate")
    assert len([c for c in mgr.calls if c[0] == "create"]) == 1  # acted exactly once


def test_unknown_repo_is_ignored() -> None:
    mgr = _FakeManager(_config())  # config owns acme/widget
    outcome = _engine(mgr).handle(_event(owner="someone", repo="else"))
    assert (outcome.action, outcome.code) == ("ignored", "unknown_repo")
    assert not mgr.calls


# ─── permission policy ────────────────────────────────────────────────────────


def test_read_only_actor_is_refused_and_replied() -> None:
    mgr = _FakeManager(_config())
    outcome = _engine(mgr).handle(_event(actor_permission="read"))
    assert (outcome.action, outcome.code) == ("refused", "insufficient_permission")
    assert "write access" in _last_reply(mgr)
    assert not any(c[0] == "create" for c in mgr.calls)


def test_allowlisted_actor_without_write_access_is_permitted() -> None:
    mgr = _FakeManager(_config(allowed_actors=["Alice"]))  # case-insensitive
    outcome = _engine(mgr).handle(_event(actor="alice", actor_permission="none"))
    assert outcome.action == "created"


def test_admin_permission_counts_as_write() -> None:
    mgr = _FakeManager(_config())
    outcome = _engine(mgr).handle(_event(actor_permission="admin"))
    assert outcome.action == "created"


# ─── best-effort reply (never re-raises into routing) ─────────────────────────


def test_reply_failure_is_swallowed() -> None:
    mgr = _FakeManager(_config())
    mgr.provider.fail = True  # every post_comment raises
    # a usage command replies; the reply blows up but routing must still return
    outcome = _engine(mgr).handle(_event(comment_body="@grove stop everything"))
    assert (outcome.action, outcome.code) == ("refused", "usage")
    assert not mgr.provider.comments  # the post failed, nothing captured


def test_reply_bodies_carry_the_signature_marker() -> None:
    """The bot's own usage/refusal replies must never re-trigger the pipeline: each
    carries the marker, so the ingest marker-guard drops it even if the bot-actor
    heuristic misses (the belt to the suspenders)."""
    mgr = _FakeManager(_config())
    outcome = _engine(mgr).handle(_event(comment_body="@grove stop everything now"))
    assert (outcome.action, outcome.code) == ("refused", "usage")
    assert SIGNATURE_MARKER in _last_reply(mgr)


def test_custom_trigger_from_config_is_honored() -> None:
    mgr = _FakeManager(_config(trigger="/grove"), match=_FakeMatch("ws-9", WorkspaceStatus.ACTIVE))
    outcome = _engine(mgr).handle(_event(comment_body="/grove tweak it"))
    assert outcome.action == "steered"
    # the old default no longer triggers under the re-pointed token
    mgr.calls.clear()
    ignored = _engine(mgr).handle(_event(comment_body="@grove tweak it", comment_id="c-2"))
    assert (ignored.action, ignored.code) == ("ignored", "not_triggered")
