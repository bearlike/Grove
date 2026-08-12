"""`compose_resource_attributes` — the pure OTEL_RESOURCE_ATTRIBUTES composer.

Pure function over (state, agent, existing) with no manager, git repo or
tmux involved, per the acceptance criteria on #455: identity stamping must
not depend on telemetry.enabled or credentials, must never clobber an
operator-set OTEL_RESOURCE_ATTRIBUTES, must escape values containing `,`/`=`,
and must omit absent/empty attributes rather than fabricate or emit blanks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from grove import __version__
from grove.core.config import AgentSpec
from grove.core.contracts.tickets import TicketRef
from grove.core.otel_resource import compose_resource_attributes
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus

T0 = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)


def _state(**overrides: object) -> WorkspaceState:
    fields: dict[str, object] = {
        "id": "ws1",
        "title": "fix-auth",
        "repo_root": "/home/u/proj",
        "branch": "grove/fix-auth",
        "base_branch": "main",
        "worktree_path": "/home/u/proj/.worktrees/fix-auth",
        "tmux_session": "grove-fix-auth",
        "agent_name": "claude",
        "status": WorkspaceStatus.RUNNING,
        "created_at": T0,
        "updated_at": T0,
    }
    fields.update(overrides)
    return WorkspaceState(**fields)  # type: ignore[arg-type]


def _agent(**overrides: object) -> AgentSpec:
    fields: dict[str, object] = {"name": "claude", "command": "claude", "kind": "claude_code"}
    fields.update(overrides)
    return AgentSpec(**fields)  # type: ignore[arg-type]


def _parse(raw: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in raw.split(","))


def test_composes_every_backed_attribute() -> None:
    state = _state(agent_session_id="sess-123", ticket_refs=[TicketRef(provider="gitea", id="42")])
    result = compose_resource_attributes(
        state, _agent(), existing=None, agent_version="2.1.226 (Claude Code)"
    )
    assert result is not None
    attrs = _parse(result)
    assert attrs == {
        "grove.workspace.id": "ws1",
        "grove.workspace.title": "fix-auth",
        "grove.repo": "proj",
        "grove.project": "proj",
        "grove.branch": "grove%2Ffix-auth",
        "grove.base_branch": "main",
        "grove.worktree": "%2Fhome%2Fu%2Fproj%2F.worktrees%2Ffix-auth",
        "grove.placement": "worktree",
        "grove.agent.name": "claude",
        "grove.agent.kind": "claude_code",
        "grove.agent.version": "2.1.226%20%28Claude%20Code%29",
        "grove.orchestrator.name": "grove",
        "grove.orchestrator.version": __version__,
        "grove.runtime": "host",
        "grove.ticket.ids": "42",
        "grove.session.id": "grove-fix-auth",
        "langfuse.session.id": "sess-123",
    }


def test_absent_or_empty_values_are_omitted_never_blank() -> None:
    # No tickets: grove.ticket.ids must not appear at all — never as a `key=`
    # blank, which an OTel reader would take for a real empty value.
    state = _state(agent_session_id=None, ticket_refs=[])
    result = compose_resource_attributes(state, _agent(), existing=None)
    assert result is not None
    assert "grove.ticket.ids" not in _parse(result)


def test_a_harness_that_mints_its_own_session_id_still_gets_a_join_key() -> None:
    """codex mints its own thread id and offers no flag to supply one, so
    `agent_session_id` is empty at launch — the only moment a resource
    attribute can be set. Falling back to Grove's own session identity is what
    keeps every span of that run in one Langfuse session; omitting the key
    scatters them across one orphan trace per internal span."""
    state = _state(agent_session_id=None, tmux_session="grove-fix-auth")
    result = compose_resource_attributes(
        state, _agent(kind="codex", name="codex", command="codex"), existing=None
    )
    assert result is not None
    attrs = _parse(result)
    assert attrs["langfuse.session.id"] == "grove-fix-auth"
    assert attrs["grove.session.id"] == "grove-fix-auth"


def test_the_harness_own_session_id_wins_when_grove_could_pin_it() -> None:
    """claude_code takes `--session-id <uuid>`, so Grove pins the harness's own
    id and the agent's native spans, its transcript exporter and Grove's spine
    all agree. The fallback must never displace that."""
    state = _state(agent_session_id="sess-123", tmux_session="grove-fix-auth")
    result = compose_resource_attributes(state, _agent(), existing=None)
    assert result is not None
    assert _parse(result)["langfuse.session.id"] == "sess-123"


def test_empty_string_fields_are_omitted() -> None:
    # A blank workspace-scoped field (e.g. a never-set title) must not
    # surface as `grove.workspace.title=` — omitted, like the absent
    # agent-session-id/ticket-ids case above. `agent.kind`/`state.runtime`/
    # `state.placement` are narrow literal types and always carry a value, and
    # the repo and project are derived from a path that always exists, so those
    # are the keys guaranteed present regardless of the rest.
    state = _state(title="", branch="", base_branch="", worktree_path="", tmux_session="")
    result = compose_resource_attributes(state, _agent(), existing=None)
    assert result is not None
    attrs = _parse(result)
    assert set(attrs) == {
        "grove.workspace.id",
        "grove.repo",
        "grove.project",
        "grove.agent.name",
        "grove.agent.kind",
        "grove.runtime",
        "grove.placement",
        # The orchestrator half is answered by the package itself, so it is
        # present whatever the workspace does or doesn't carry.
        "grove.orchestrator.name",
        "grove.orchestrator.version",
    }


def test_operator_set_key_wins_a_collision() -> None:
    state = _state(agent_session_id="sess-123")
    existing = "grove.workspace.id=operator-chosen,team=payments"
    result = compose_resource_attributes(state, _agent(), existing=existing)
    assert result is not None
    attrs = _parse(result)
    # Operator's own grove.workspace.id survives untouched.
    assert attrs["grove.workspace.id"] == "operator-chosen"
    assert attrs["team"] == "payments"
    # Grove still fills every key the operator didn't name.
    assert attrs["langfuse.session.id"] == "sess-123"
    assert attrs["grove.branch"] == "grove%2Ffix-auth"


def test_values_with_commas_and_equals_are_escaped_and_do_not_corrupt_neighbors() -> None:
    state = _state(title="release, v1=final", branch="feature/a,b=c")
    result = compose_resource_attributes(state, _agent(), existing=None)
    assert result is not None
    attrs = _parse(result)
    # Round-trips: exactly one entry per key, no spurious splits from the
    # raw comma/equals inside the value.
    assert attrs["grove.workspace.title"] == "release%2C%20v1%3Dfinal"
    assert attrs["grove.branch"] == "feature%2Fa%2Cb%3Dc"
    assert len(attrs) == len(result.split(","))


def test_container_runtime_is_stamped() -> None:
    state = _state(runtime=Runtime.CONTAINER)
    result = compose_resource_attributes(state, _agent(), existing=None)
    assert result is not None
    assert _parse(result)["grove.runtime"] == "container"


def test_multiple_ticket_ids_join_before_encoding() -> None:
    state = _state(
        ticket_refs=[
            TicketRef(provider="gitea", id="42"),
            TicketRef(provider="gitea", id="43"),
        ]
    )
    result = compose_resource_attributes(state, _agent(), existing=None)
    assert result is not None
    assert _parse(result)["grove.ticket.ids"] == "42%2C43"


def test_the_orchestrator_names_itself_and_its_own_build() -> None:
    """A trace has to say which program launched it and which build of that
    program — read from the package, never a hard-coded literal."""
    result = compose_resource_attributes(_state(), _agent(), existing=None)
    assert result is not None
    attrs = _parse(result)
    assert attrs["grove.orchestrator.name"] == "grove"
    assert attrs["grove.orchestrator.version"] == __version__


def test_an_unanswerable_agent_version_is_omitted_never_unknown() -> None:
    """mewbo/generic report no version, and a probe of a wedged or missing
    binary answers `None`. Either way the key is absent — a literal
    `unknown` would be a claim, and a blank would read as a real empty value."""
    result = compose_resource_attributes(_state(), _agent(), existing=None, agent_version=None)
    assert result is not None
    assert "grove.agent.version" not in _parse(result)


def test_the_agent_version_is_recorded_verbatim() -> None:
    """The vendor's own string, escaped for the wire but not normalized — the
    provider boundary forbids parsing a semver out of it."""
    result = compose_resource_attributes(
        _state(),
        _agent(kind="codex", name="codex", command="codex"),
        existing=None,
        agent_version="codex-cli 0.147.0",
    )
    assert result is not None
    assert _parse(result)["grove.agent.version"] == "codex-cli%200.147.0"


def test_an_operator_may_override_the_orchestrator_and_agent_identity_too() -> None:
    """The identity keys are not privileged: they follow the same "explicit
    wins" rule as every other key Grove contributes."""
    existing = "grove.orchestrator.version=pinned,grove.agent.version=pinned-too"
    result = compose_resource_attributes(
        _state(), _agent(), existing=existing, agent_version="2.1.226 (Claude Code)"
    )
    assert result is not None
    attrs = _parse(result)
    assert attrs["grove.orchestrator.version"] == "pinned"
    assert attrs["grove.agent.version"] == "pinned-too"
