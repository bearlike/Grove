"""Pure unit tests for the BranchPlan discriminated union.

These tests pin the contract every Grove client speaks: the four
variants validate the right field shapes, dispatch by `kind`, and
``resolve()`` produces the right ``ResolvedBranch`` for each. No
fixtures, no I/O, no manager — only the contract module.

Integration tests for the engine consuming these plans live in
`test_workspace_lifecycle.py`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from grove.core import (
    AutoBranch,
    CreateWorkspaceRequest,
    ExistingLocalBranch,
    NewNamedBranch,
    TrackRemoteBranch,
)
from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import BranchMode
from grove.core.workspace import BranchProvenance


@pytest.fixture
def cfg() -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"branch_prefix": "grove/"},
            "tmux": {"session_prefix": "grove-"},
        }
    )


# ─── resolution ──────────────────────────────────────────────────────────────


def test_auto_branch_resolves_to_prefixed_slug_with_ts(cfg: GroveConfig) -> None:
    plan = AutoBranch(base_ref="main")
    resolved = plan.resolve(cfg, "Hello World", "20260507-103014")
    assert resolved.name == "grove/hello-world-20260507-103014"
    assert resolved.base_ref == "main"
    assert resolved.mode == BranchMode.NEW
    assert resolved.provenance == BranchProvenance.GROVE_CREATED
    assert resolved.tracks is None


def test_new_named_branch_uses_user_name_verbatim(cfg: GroveConfig) -> None:
    plan = NewNamedBranch(name="feature/payment-v2", base_ref="develop")
    resolved = plan.resolve(cfg, "ignored", "20260507-103014")
    assert resolved.name == "feature/payment-v2"
    assert resolved.base_ref == "develop"
    assert resolved.mode == BranchMode.NEW
    assert resolved.provenance == BranchProvenance.GROVE_CREATED


def test_existing_local_branch_resolves_to_checkout_attached(cfg: GroveConfig) -> None:
    plan = ExistingLocalBranch(name="feature/x")
    resolved = plan.resolve(cfg, "ignored", "20260507-103014")
    assert resolved.name == "feature/x"
    assert resolved.base_ref is None
    assert resolved.mode == BranchMode.CHECKOUT
    # User-attached on purpose: kill must not delete the user's branch by default.
    assert resolved.provenance == BranchProvenance.USER_ATTACHED


def test_track_remote_derives_local_name_from_remote_ref(cfg: GroveConfig) -> None:
    plan = TrackRemoteBranch(remote_ref="origin/feature/x")
    resolved = plan.resolve(cfg, "ignored", "20260507-103014")
    # 'origin/feature/x' → strip first segment → 'feature/x'
    assert resolved.name == "feature/x"
    assert resolved.base_ref == "origin/feature/x"
    assert resolved.mode == BranchMode.NEW
    # The local tracking branch is brand new — we created it.
    assert resolved.provenance == BranchProvenance.GROVE_CREATED
    assert resolved.tracks == "origin/feature/x"


def test_track_remote_local_name_override(cfg: GroveConfig) -> None:
    plan = TrackRemoteBranch(remote_ref="origin/long/path/to/feat", local_name="feat")
    resolved = plan.resolve(cfg, "ignored", "20260507-103014")
    assert resolved.name == "feat"
    assert resolved.tracks == "origin/long/path/to/feat"


# ─── pydantic validation (boundary) ──────────────────────────────────────────


def test_new_named_rejects_leading_dash() -> None:
    with pytest.raises(ValidationError):
        NewNamedBranch(name="-bad", base_ref="main")


def test_new_named_rejects_empty_name() -> None:
    with pytest.raises(ValidationError):
        NewNamedBranch(name="", base_ref="main")


def test_track_remote_requires_slash_separated_ref() -> None:
    with pytest.raises(ValidationError):
        TrackRemoteBranch(remote_ref="no-slash-here")


def test_branch_plan_extra_fields_rejected() -> None:
    """``extra='forbid'`` on every variant catches client typos at the boundary."""
    with pytest.raises(ValidationError):
        AutoBranch.model_validate({"base_ref": "main", "extra_field": "nope"})


# ─── discriminator dispatch ──────────────────────────────────────────────────


def test_request_dispatches_to_right_variant_by_kind() -> None:
    """``kind`` is the wire-level discriminator; the engine never has to guess."""
    payload = {
        "agent_name": "claude",
        "title": "demo",
        "branch_plan": {"kind": "track_remote", "remote_ref": "origin/feature/x"},
    }
    req = CreateWorkspaceRequest.model_validate(payload)
    assert isinstance(req.branch_plan, TrackRemoteBranch)
    assert req.branch_plan.remote_ref == "origin/feature/x"


def test_request_default_branch_plan_is_auto_branch() -> None:
    """Callers that don't care about branch semantics get the historical
    Grove behavior for free — Grove auto-generates the branch."""
    req = CreateWorkspaceRequest(agent_name="claude", title="demo")
    assert isinstance(req.branch_plan, AutoBranch)
    assert req.branch_plan.base_ref == "HEAD"


def test_request_round_trips_through_json() -> None:
    """JSON round-trip is the smoke test for a future API server: a client
    sends the JSON, the engine validates it, the engine returns the same
    structure to the next client. Discriminated unions need ``kind`` to
    survive serialization for this to work."""
    original = CreateWorkspaceRequest(
        agent_name="claude",
        title="demo",
        branch_plan=NewNamedBranch(name="feature/x", base_ref="main"),
    )
    payload = original.model_dump_json()
    restored = CreateWorkspaceRequest.model_validate_json(payload)
    assert isinstance(restored.branch_plan, NewNamedBranch)
    assert restored.branch_plan.name == "feature/x"
