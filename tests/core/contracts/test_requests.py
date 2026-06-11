"""CreateWorkspaceRequest + UpdateWorkspaceRequest wire-shape tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.requests import (
    CreateWorkspaceRequest,
    UpdateWorkspaceRequest,
)


def test_create_request_skip_init_defaults_false() -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t")
    assert req.skip_init is False


def test_create_request_accepts_skip_init_and_root_plan() -> None:
    """skip_init + the root variant both survive a JSON round-trip — the smoke
    test for the future API server constructing this from form fields."""
    req = CreateWorkspaceRequest(
        agent_name="claude", title="t", branch_plan=RootBranch(), skip_init=True
    )
    assert req.skip_init is True
    assert isinstance(req.branch_plan, RootBranch)
    reloaded = CreateWorkspaceRequest.model_validate_json(req.model_dump_json())
    assert reloaded.skip_init is True
    assert isinstance(reloaded.branch_plan, RootBranch)


def test_request_default_no_repo_root() -> None:
    """Existing in-process callers (TUI) construct requests without repo_root."""
    req = CreateWorkspaceRequest(agent_name="claude", title="t")
    assert req.repo_root is None


def test_request_with_repo_root() -> None:
    """Daemon callers must pass repo_root so the right Manager handles it."""
    req = CreateWorkspaceRequest(agent_name="claude", title="t", repo_root=Path("/repos/myproj"))
    assert req.repo_root == Path("/repos/myproj")


def test_request_round_trip_via_json_with_repo_root() -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t", repo_root=Path("/repos/myproj"))
    reloaded = CreateWorkspaceRequest.model_validate_json(req.model_dump_json())
    assert reloaded == req


def test_create_request_accepts_optional_description() -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t", description="see ticket #1")
    assert req.description == "see ticket #1"


def test_create_request_description_defaults_none() -> None:
    req = CreateWorkspaceRequest(agent_name="claude", title="t")
    assert req.description is None


def test_create_request_rejects_overlong_description() -> None:
    with pytest.raises(ValidationError):
        CreateWorkspaceRequest(agent_name="claude", title="t", description="x" * 2001)


# ─── UpdateWorkspaceRequest ────────────────────────────────────────────────


def test_update_request_title_only() -> None:
    req = UpdateWorkspaceRequest(title="renamed")
    assert req.title == "renamed"
    assert req.description is None


def test_update_request_description_only() -> None:
    req = UpdateWorkspaceRequest(description="ticket")
    assert req.title is None
    assert req.description == "ticket"


def test_update_request_both_fields() -> None:
    req = UpdateWorkspaceRequest(title="renamed", description="why")
    assert req.title == "renamed"
    assert req.description == "why"


def test_update_request_clear_description_with_empty_string() -> None:
    req = UpdateWorkspaceRequest(description="")
    assert req.description == ""


def test_update_request_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        UpdateWorkspaceRequest()


def test_update_request_rejects_empty_title() -> None:
    with pytest.raises(ValidationError):
        UpdateWorkspaceRequest(title="")


def test_update_request_rejects_overlong_title() -> None:
    with pytest.raises(ValidationError):
        UpdateWorkspaceRequest(title="x" * 121)


def test_update_request_rejects_overlong_description() -> None:
    with pytest.raises(ValidationError):
        UpdateWorkspaceRequest(description="x" * 2001)


def test_update_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        UpdateWorkspaceRequest.model_validate({"title": "renamed", "agent_name": "claude"})


def test_update_request_round_trip_via_json() -> None:
    req = UpdateWorkspaceRequest(title="renamed", description="why")
    reloaded = UpdateWorkspaceRequest.model_validate_json(req.model_dump_json())
    assert reloaded == req
