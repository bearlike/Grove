"""Container identity, argv builders, and the lifecycle verbs.

The docker/devcontainer subprocess boundaries are faked; everything above them
is the real code path. The centerpiece is the teardown invariant — *no Grove
teardown command may name a container it did not create* — so several tests are
adversarial rather than illustrative: a decoy container on the host, a short id,
a foreign compose project, an `up` that reports a project Grove never exported.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError

from grove.core.container_infra import GroveLabels, InfrastructureScope
from grove.core.container_runtime import (
    ContainerLifecycle,
    ContainerObservation,
    ContainerRuntimeState,
    ContainerState,
    DockerCli,
)
from grove.core.devcontainer import UpResult
from grove.core.errors import ContainerError
from tests.conftest import (
    DOCKER_INSPECT_RESTARTED,
    DOCKER_INSPECT_RESTARTED_AT,
    DOCKER_INSPECT_RUNNING,
    DOCKER_INSPECT_STARTED_AT,
    DOCKER_INSPECT_STOPPED,
)

FULL_ID = "a" * 64
OTHER_ID = "b" * 64
DECOY_ID = "d" * 64  # a container on this host that Grove did NOT create


class FakeDocker(DockerCli):
    """Records every argv and replays canned results — the process boundary only.

    Stands in at ``read_result`` rather than ``read`` so the rc→``None``
    translation stays the REAL code: a read path has to tell "docker
    exited 1, there is no such container" apart from "docker never ran", and a
    double that answers with the post-translation shape can express only one of
    them. An unmatched read replays docker's own answer for a container that is
    not there — rc 1, empty stdout, ``error: no such object`` on stderr —
    captured verbatim from Docker 29.6.1.
    """

    def __init__(
        self,
        *,
        reads: dict[str, str] | None = None,
        fail: str | None = None,
        unavailable: bool = False,
    ) -> None:
        super().__init__()
        self.commands: list[list[str]] = []
        self._reads = reads or {}
        self._fail = fail
        self._unavailable = unavailable

    def run(self, argv: Sequence[str], *, action: str) -> str:
        self.commands.append(list(argv))
        if self._fail is not None and self._fail in " ".join(argv):
            raise ContainerError(f"failed to {action}: boom")
        return ""

    def read_result(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
        self.commands.append(list(argv))
        if self._unavailable:
            return None
        joined = " ".join(argv)
        for needle, out in self._reads.items():
            if needle in joined:
                return subprocess.CompletedProcess(list(argv), 0, out, "")
        return subprocess.CompletedProcess(
            list(argv), 1, "", f"error: no such object: {argv[-1]}\n"
        )

    @property
    def named(self) -> set[str]:
        """Every container id / project name any emitted command referenced."""
        tokens: set[str] = set()
        for argv in self.commands:
            tokens.update(argv)
        return tokens


def _state(**fields: object) -> ContainerRuntimeState:
    base: dict[str, object] = {
        "container_id": FULL_ID,
        "image_ref": "ghcr.io/example/dev:1",
        "remote_user": "vscode",
        "remote_workspace_folder": "/workspaces/repo",
        "id_labels": ContainerRuntimeState.labels_for("ws1"),
        "provisioned": True,
        # The start the captured RUNNING payload reports, so the default state
        # is a container Grove provisioned AS IT NOW STANDS. A record
        # without this witness is a different case and has its own tests.
        "provisioned_start": DOCKER_INSPECT_STARTED_AT,
    }
    base.update(fields)
    return ContainerRuntimeState.model_validate(base)


def _lifecycle(
    state: ContainerRuntimeState,
    *,
    docker: FakeDocker | None = None,
) -> tuple[ContainerLifecycle, FakeDocker]:
    fake = docker or FakeDocker()
    return ContainerLifecycle(state, docker=fake), fake


# ─── identity + derived facts ────────────────────────────────────────────────


def test_compose_project_is_the_mode_discriminator() -> None:
    assert _state().is_compose is False
    assert _state(compose_project="repo_devcontainer").is_compose is True


def test_the_live_substate_is_never_a_persisted_field() -> None:
    """The identity stores durable facts only; `ContainerState` is a live READ.

    It used to be a field, written at provision and never refreshed, so a
    container the user stopped by hand still read RUNNING on every surface that
    renders the record. `extra="forbid"` is what makes the removal enforceable:
    a caller trying to persist a substate now fails loudly instead of writing a
    value nothing updates.
    """
    with pytest.raises(ValidationError):
        ContainerRuntimeState.model_validate({"state": ContainerState.RUNNING})
    assert "state" not in ContainerRuntimeState.model_fields


def test_unprovisioned_is_distinct_from_running_for_a_live_container() -> None:
    """The state that looks healthy to `docker ps` while the firewall is down."""
    lifecycle, _ = _lifecycle(
        _state(provisioned=False), docker=FakeDocker(reads={"inspect": DOCKER_INSPECT_RUNNING})
    )
    assert lifecycle.status() is ContainerState.UNPROVISIONED


# ─── provisioning is a fact about a START, not about an id ──────────────────


def test_a_container_restarted_outside_grove_stops_reading_provisioned() -> None:
    """The bug: a bare `docker start` runs no `postStartCommand`, i.e. no firewall.

    Same container, same id, same recorded `provisioned=True` — the ONLY thing
    that changed is `.State.StartedAt`, and that is the whole difference between
    a container Grove hardened and one running the agent's blast-radius boundary
    with no boundary in it. Both payloads are captured from the real engine, so
    the fact under test is docker's own behaviour rather than a fixture's.
    """
    state = _state()
    live, _ = _lifecycle(state, docker=FakeDocker(reads={"inspect": DOCKER_INSPECT_RUNNING}))
    assert live.status() is ContainerState.RUNNING

    restarted, _ = _lifecycle(state, docker=FakeDocker(reads={"inspect": DOCKER_INSPECT_RESTARTED}))
    assert restarted.status() is ContainerState.UNPROVISIONED


def test_a_record_with_no_start_witness_fails_closed() -> None:
    """ "Cannot tell which start was provisioned" is not "the firewall is up".

    A container record with no recorded start witness (including one whose
    mint-time `docker inspect` could not be read) is in exactly this state. The
    remedy is a `respawn`, which re-provisions and records the witness.
    """
    legacy, _ = _lifecycle(
        _state(provisioned_start=""), docker=FakeDocker(reads={"inspect": DOCKER_INSPECT_RUNNING})
    )
    assert legacy.status() is ContainerState.UNPROVISIONED


def test_a_docker_that_answers_without_the_start_field_is_not_vouched_for() -> None:
    """A short read is a boundary Grove misread — degrade, never IndexError.

    This runs on the poll path for every container workspace, so a docker whose
    output has fewer fields than the format asked for must cost an honest
    "cannot vouch" rather than an exception inside reconciliation.
    """
    short = "true\trunning\tghcr.io/example/dev:1\n"
    lifecycle, _ = _lifecycle(_state(), docker=FakeDocker(reads={"inspect": short}))
    assert lifecycle.status() is ContainerState.UNPROVISIONED


def test_the_start_witness_is_an_identity_not_an_ordering() -> None:
    """Equality against docker's OWN value, so no two clocks have to agree.

    A "provisioned at" host timestamp compared against `.State.StartedAt` would
    be correct only while Grove and the docker daemon share a clock, which stops
    being true the moment `DOCKER_HOST` points at a remote engine.
    """
    state = _state()
    assert state.provisioned_for(DOCKER_INSPECT_STARTED_AT) is True
    assert state.provisioned_for(DOCKER_INSPECT_RESTARTED_AT) is False
    assert state.provisioned_for("") is False
    assert _state(provisioned_start="").provisioned_for(DOCKER_INSPECT_STARTED_AT) is False


def test_the_mint_records_which_start_it_provisioned() -> None:
    """The PRODUCER, driven through `from_up_result`.

    A guard whose state no production path ever writes reads as handled in a
    green suite while the behaviour is "never happens", so the witness is
    asserted where it is minted rather than only where it is compared.
    """
    assert _minted().provisioned_start == DOCKER_INSPECT_STARTED_AT


def test_a_re_provision_that_cannot_read_the_container_drops_the_start_witness() -> None:
    """Unlike every other field here, this one is NOT carried forward from `base`.

    A stale volume name only fails a best-effort removal; a stale start witness
    would assert a firewall Grove can no longer prove is there.
    """
    blind = ContainerRuntimeState.from_up_result(
        UpResult(outcome="success", containerId=FULL_ID), base=_state(), observed=None
    )
    assert blind.provisioned_start == ""


def test_the_mint_read_asks_docker_which_start_it_is_on() -> None:
    """The detection costs one template field in a read that already happened."""
    assert ".State.StartedAt" in ContainerRuntimeState.OBSERVE_FORMAT
    assert ".State.StartedAt" in " ".join(_state().inspect_argv())


def test_status_maps_stopped_and_absent_and_missing_image() -> None:
    stopped, _ = _lifecycle(_state(), docker=FakeDocker(reads={"inspect": DOCKER_INSPECT_STOPPED}))
    assert stopped.status() is ContainerState.STOPPED

    # Docker answered "no such object" for both the container and its image.
    gone, _ = _lifecycle(_state(), docker=FakeDocker(reads={}))
    assert gone.status() is ContainerState.MISSING_IMAGE

    present, _ = _lifecycle(_state(), docker=FakeDocker(reads={"image inspect": "sha256:1\n"}))
    assert present.status() is ContainerState.ABSENT


def test_an_unreachable_docker_is_not_an_absent_container() -> None:
    """ "Docker never ran" must never arrive dressed as an answer.

    `read_result` is what keeps the two apart: a non-zero exit is docker saying
    the container is gone, while a binary that could not be started at all says
    nothing about the container — and reconciliation acts on this value, so
    conflating them would report every container workspace on the host as dead
    while all of them keep running.
    """
    blind, docker = _lifecycle(_state(), docker=FakeDocker(unavailable=True))
    assert blind.status() is None
    # Cheap by construction: the blind arm costs one attempted fork, not a
    # second one probing for the image.
    assert len(docker.commands) == 1


# ─── the teardown invariant ──────────────────────────────────────────────────


def test_every_filter_carries_the_managed_label() -> None:
    flags = _state().filters()
    assert "label=grove.managed=1" in flags
    assert "label=grove.workspace=ws1" in flags


def test_enumeration_never_truncates_ids() -> None:
    argv = _state().enumerate_argv()
    assert "--no-trunc" in argv
    assert argv[:4] == ["docker", "ps", "-a", "-q"]
    assert "--filter" in argv


def test_teardown_refuses_a_short_container_id() -> None:
    """`docker rm -f` resolves its argument as a PREFIX — short ids are banned."""
    with pytest.raises(ContainerError, match="PREFIX"):
        _state(container_id=FULL_ID[:12]).teardown_argv()


def test_teardown_by_id_uses_the_full_64_char_form() -> None:
    argv = _state().teardown_argv()
    assert argv == ["docker", "rm", "-f", FULL_ID]
    assert FULL_ID[:12] not in argv


def test_compose_teardown_never_passes_dash_v() -> None:
    argv = _state(compose_project="repo_devcontainer", compose_owned=True).teardown_argv()
    assert argv == ["docker", "compose", "-p", "repo_devcontainer", "down", "--remove-orphans"]
    assert "-v" not in argv
    assert "--volumes" not in argv


def test_project_scoped_commands_refuse_a_stack_grove_cannot_prove_it_created() -> None:
    """Ownership is a recorded fact; without it there is no project-scoped arm."""
    foreign = _state(compose_project="production", compose_owned=False)
    with pytest.raises(ContainerError, match="could not verify"):
        foreign.teardown_argv()
    with pytest.raises(ContainerError, match="could not verify"):
        foreign.stop_argv()
    # Even an owned stack needs the id of the container that corroborated it.
    with pytest.raises(ContainerError, match="PREFIX"):
        _state(
            compose_project="repo_devcontainer", compose_owned=True, container_id=FULL_ID[:12]
        ).teardown_argv()


def test_up_that_reports_an_unexpected_project_fails_closed() -> None:
    result = UpResult(
        outcome="success", containerId=FULL_ID, composeProjectName="somebody-elses-stack"
    )
    minted = ContainerRuntimeState.from_up_result(result, expected_project="repo_devcontainer")
    # It is still a compose workspace — that is the MODE — but nothing may be
    # aimed at the project, so teardown degrades to label-filtered removal.
    assert minted.is_compose is True
    assert minted.compose_owned is False
    with pytest.raises(ContainerError, match="could not verify"):
        minted.teardown_argv()


def test_volume_removal_is_restricted_to_grove_owned_volumes() -> None:
    state = _state(owned_volumes=["grove-ws1-node-modules"])
    assert state.volume_argv("grove-ws1-node-modules")[-1] == "grove-ws1-node-modules"
    with pytest.raises(ContainerError, match="not owned"):
        state.volume_argv("postgres_data")


# ─── supervision asserted after `up` ─────────────────────────────────────────


def test_the_restart_policy_is_asserted_by_id_and_refuses_a_short_one() -> None:
    """Compose ignores `runArgs`, so creation-time assertion never reached a stack.

    An `always` container resurrects a PAUSED workspace when the docker daemon
    restarts, and a docker-level restart does not re-run `postStartCommand` — so
    the agent returns with no egress firewall. Post-hoc is how the compose path
    gets the property at all, and it is still a command that NAMES a container.
    """
    assert ContainerRuntimeState.restart_policy_argv(FULL_ID) == [
        "docker",
        "update",
        "--restart",
        "no",
        FULL_ID,
    ]
    with pytest.raises(ContainerError, match="PREFIX"):
        ContainerRuntimeState.restart_policy_argv(FULL_ID[:12])


def test_a_record_written_before_a_field_was_retired_still_loads() -> None:
    """`extra="forbid"` makes deleting a field a breaking change on disk.

    `_decode_container` re-validates LOUDLY on purpose — a dropped container
    record orphans a real container nothing can later name — so retiring
    `compose_services` without this would have failed every existing compose
    workspace at the next daemon start. Naming the retired key keeps the loud
    failure for a key Grove never wrote, which is the half worth keeping.
    """
    legacy = ContainerRuntimeState.model_validate(
        {"container_id": FULL_ID, "compose_services": ["app", "mongo"]}
    )
    assert legacy.container_id == FULL_ID
    assert "compose_services" not in ContainerRuntimeState.model_fields
    with pytest.raises(ValidationError):
        ContainerRuntimeState.model_validate({"container_id": FULL_ID, "never_ours": 1})


# ─── who owns a volume ────────────────────────────────────────────────────────

#: The per-container volume the docker-in-docker feature minted for that run —
#: the substituted form of ``dind-var-lib-docker-${devcontainerId}``.
_DIND_VOLUME = "dind-var-lib-docker-108mj013troq3268pli9v6g1hdhmcmfpfv2e6feag7rho6cf62th"

#: A REAL ``docker inspect -f '{{json .Mounts}}'`` mount table, captured from a
#: Grove container workspace whose devcontainer config declared the
#: docker-in-docker feature, one shared cache volume and one project volume.
#: Verbatim in shape and in every volume name; only the host paths are
#: genericized. It is here rather than hand-written because a guard tested
#: against invented state would miss real mount shapes — the three volume
#: entries below are the exact three categories a teardown has to tell apart,
#: and only one is Grove's.
_ATTACHED_MOUNTS: list[dict[str, object]] = [
    {
        "Type": "bind",
        "Source": "/home/dev/.claude/settings.json",
        "Destination": "/grove/agent-config/claude_code/settings.json",
        "RW": False,
    },
    {
        "Type": "bind",
        "Source": "/srv/projects/repo/.worktrees/volprobe-20260730-192927",
        "Destination": "/workspaces/volprobe-20260730-192927",
        "RW": True,
    },
    {
        "Type": "volume",
        "Name": _DIND_VOLUME,
        "Source": f"/var/lib/docker/volumes/{_DIND_VOLUME}/_data",
        "Destination": "/var/lib/docker",
        "Driver": "local",
        "RW": True,
    },
    {
        "Type": "volume",
        "Name": "volprobe-shared-cache",
        "Source": "/var/lib/docker/volumes/volprobe-shared-cache/_data",
        "Destination": "/caches/shared",
        "Driver": "local",
        "RW": True,
    },
    {
        "Type": "volume",
        "Name": "volprobe-db-data",
        "Source": "/var/lib/docker/volumes/volprobe-db-data/_data",
        "Destination": "/data",
        "Driver": "local",
        "RW": True,
    },
]

#: The ``mounts`` array of the complete override config Grove itself wrote for
#: that same container, captured from the worktree. The feature's own entry
#: reaches Grove with ``${devcontainerId}`` UNSUBSTITUTED — the CLI expands it at
#: `up` time — which is the entire reason the lifetime question is answerable.
_DECLARED_MOUNTS: list[object] = [
    {
        "source": "dind-var-lib-docker-${devcontainerId}",
        "target": "/var/lib/docker",
        "type": "volume",
    },
    "source=volprobe-shared-cache,target=/caches/shared,type=volume",
    "source=volprobe-db-data,target=/data,type=volume",
    "type=bind,source=/home/dev/.claude/settings.json,"
    "target=/grove/agent-config/claude_code/settings.json,readonly",
]


def _observed(**overrides: object) -> ContainerObservation:
    """The one mint-time `docker inspect`, as the real command returns it."""
    fields: dict[str, object] = {
        "labels": {
            ContainerRuntimeState.MANAGED_LABEL_KEY: ContainerRuntimeState.MANAGED_LABEL_VALUE,
            ContainerRuntimeState.WORKSPACE_LABEL_KEY: "ws1",
        },
        "mounts": tuple(_ATTACHED_MOUNTS),
        "image": "vsc-repo-9f2a-uid",
        "started_at": DOCKER_INSPECT_STARTED_AT,
    }
    fields.update(overrides)
    return ContainerObservation(**fields)  # type: ignore[arg-type]


def _minted(**kwargs: object) -> ContainerRuntimeState:
    kwargs.setdefault("observed", _observed())
    return ContainerRuntimeState.from_up_result(
        UpResult(outcome="success", containerId=FULL_ID),
        declared_mounts=_DECLARED_MOUNTS,
        **kwargs,
    )


def test_the_mint_populates_owned_volumes_from_the_containers_own_mount_table() -> None:
    """The producer, not the guard.

    `owned_volumes` is a well-documented, correctly-guarded field; asserting only
    the guard leaves the producer untested, and a producer no code ever calls
    means every container workspace leaks its per-container volume while a
    suite built on hand-crafted state stays green.
    """
    assert _minted().owned_volumes == [_DIND_VOLUME]


def test_a_shared_cache_volume_is_never_one_workspaces_to_remove() -> None:
    """It is declared to outlive every workspace, and its neighbours are using it."""
    minted = _minted()
    assert "volprobe-shared-cache" not in minted.owned_volumes
    with pytest.raises(ContainerError, match="not owned"):
        minted.volume_argv("volprobe-shared-cache")


def test_a_project_declared_volume_is_never_owned_even_beside_a_per_container_one() -> None:
    """A compose stack's volumes never appear in `mounts` at all — and may be a database.

    The dangerous shape is the inverse rule ("anything attached that we did not
    declare is ours"), which would sweep exactly this. Ownership is positive:
    only an instantiated `${devcontainerId}` source qualifies.
    """
    attached = [*_ATTACHED_MOUNTS, {"Type": "volume", "Name": "myapp_postgres_data"}]
    owned = ContainerRuntimeState.owned_volumes_in(declared=_DECLARED_MOUNTS, attached=attached)
    assert owned == [_DIND_VOLUME]
    assert "volprobe-db-data" not in owned
    assert "myapp_postgres_data" not in owned


def test_a_source_too_broad_to_attribute_owns_nothing() -> None:
    """Fail toward under-deleting: a leak is recoverable, somebody's data is not."""
    attached = [
        {"Type": "volume", "Name": "cache-shared"},
        {"Type": "volume", "Name": "cache-1a2b3c"},
    ]
    # A bare token would match every volume on the container.
    assert (
        ContainerRuntimeState.owned_volumes_in(
            declared=["source=${devcontainerId},target=/x,type=volume"], attached=attached
        )
        == []
    )
    # A prefix a stable name also happens to satisfy: the literal declaration
    # wins over the pattern, so only the substituted one is claimed.
    assert ContainerRuntimeState.owned_volumes_in(
        declared=[
            "source=cache-${devcontainerId},target=/x,type=volume",
            "source=cache-shared,target=/y,type=volume",
        ],
        attached=attached,
    ) == ["cache-1a2b3c"]


def test_a_config_declaring_no_per_container_volume_owns_nothing() -> None:
    """No pattern, no claim — however many volumes the container happens to mount."""
    declared = ["source=devc-repo-uv,target=/caches/uv,type=volume"]
    assert (
        ContainerRuntimeState.owned_volumes_in(declared=declared, attached=_ATTACHED_MOUNTS) == []
    )


def test_a_re_provision_keeps_a_volume_it_already_owned() -> None:
    """A forgotten entry is a silent leak; a stale one only fails a best-effort rm."""
    base = _state(owned_volumes=["dind-var-lib-docker-earlier"])
    refreshed = ContainerRuntimeState.from_up_result(
        UpResult(outcome="success", containerId=FULL_ID), base=base
    )
    assert refreshed.owned_volumes == ["dind-var-lib-docker-earlier"]
    assert _minted(base=base).owned_volumes == [_DIND_VOLUME]


def test_the_mint_read_refuses_a_short_container_id() -> None:
    """The mint-time read is still a command that names a container."""
    argv = ContainerRuntimeState.observe_argv(FULL_ID)
    assert argv[:3] == ["docker", "inspect", "-f"]
    assert argv[-1] == FULL_ID
    with pytest.raises(ContainerError, match="PREFIX"):
        ContainerRuntimeState.observe_argv(FULL_ID[:12])


def test_the_mint_read_returns_one_parseable_object_for_all_three_answers() -> None:
    """The format and its parser are one unit; a JSON object, never a split line.

    Docker renders the payload verbatim, so a mount source containing a tab (a
    legal path) would break any in-band separator. Pinning the template against
    a captured payload is what keeps the format string and `from_json` honest.
    """
    raw = (
        '{"labels":{"grove.managed":"1","com.docker.compose.project":"repo_devcontainer"},'
        '"mounts":[{"Type":"volume","Name":"repo_devcontainer_dind-var-lib-docker-abc"}],'
        '"image":"vsc-repo-9f2a-uid"}'
    )
    observed = ContainerObservation.from_json(raw)
    assert observed is not None
    assert observed.labels["com.docker.compose.project"] == "repo_devcontainer"
    assert observed.image == "vsc-repo-9f2a-uid"
    assert observed.mounts[0]["Name"] == "repo_devcontainer_dind-var-lib-docker-abc"
    # "Could not tell" is None, never an empty observation: the consumers act on
    # the difference between blindness and an honest nothing.
    assert ContainerObservation.from_json("") is None
    assert ContainerObservation.from_json("not json") is None
    assert ContainerObservation.from_json(None) is None
    # Docker renders a container with no labels as `null`, which is a real read.
    partial = ContainerObservation.from_json('{"labels":null,"mounts":[],"image":"img"}')
    assert partial is not None
    assert partial.labels == {} and partial.image == "img"


def test_the_mint_records_the_image_up_never_reports() -> None:
    """`up` carries no image, so the field behind MISSING_IMAGE was always empty."""
    assert _minted().image_ref == "vsc-repo-9f2a-uid"


# ─── who owns a compose stack ─────────────────────────────────────────────────

_PROJECT = "composeprobe-20260730-194640_devcontainer"
"""A REAL project name. The devcontainer CLI mints it from the worktree
basename plus `_devcontainer` — never a `grove-` prefix, so ownership cannot be
verified by pattern-matching the name."""


def _compose_observed(**overrides: object) -> ContainerObservation:
    overrides.setdefault(
        "labels",
        {
            ContainerRuntimeState.MANAGED_LABEL_KEY: ContainerRuntimeState.MANAGED_LABEL_VALUE,
            ContainerRuntimeState.COMPOSE_PROJECT_LABEL: _PROJECT,
            "com.docker.compose.service": "app",
        },
    )
    return _observed(**overrides)


def _compose_minted(**kwargs: object) -> ContainerRuntimeState:
    kwargs.setdefault("observed", _compose_observed())
    return ContainerRuntimeState.from_up_result(
        UpResult(outcome="success", containerId=FULL_ID, composeProjectName=_PROJECT),
        declared_mounts=_DECLARED_MOUNTS,
        **kwargs,
    )


def test_the_mint_owns_a_stack_its_own_container_vouches_for() -> None:
    """Ownership comes from evidence the container itself vouches for, never a
    pattern in the compose project name — a `grove-` prefix on a name Grove does
    not mint would reject every real stack, leaving only the label-filtered
    fallback (which reaches the primary service only).
    """
    minted = _compose_minted()
    assert minted.compose_project == _PROJECT
    assert minted.compose_owned is True
    assert minted.teardown_argv() == [
        "docker",
        "compose",
        "-p",
        _PROJECT,
        "down",
        "--remove-orphans",
    ]


def test_a_stack_whose_container_disagrees_or_is_unreadable_is_never_owned() -> None:
    """Three ways to be unsure, and all three fail closed — but stay compose."""
    blind = _compose_minted(observed=None)
    unlabelled = _compose_minted(observed=_compose_observed(labels={}))
    mismatched = _compose_minted(
        observed=_compose_observed(
            labels={
                ContainerRuntimeState.MANAGED_LABEL_KEY: ContainerRuntimeState.MANAGED_LABEL_VALUE,
                ContainerRuntimeState.COMPOSE_PROJECT_LABEL: "somebody-elses-stack",
            }
        )
    )
    for state in (blind, unlabelled, mismatched):
        assert state.is_compose is True, "the MODE is still compose — that is not in doubt"
        assert state.compose_owned is False
        with pytest.raises(ContainerError, match="could not verify"):
            state.teardown_argv()


def test_a_stacks_own_volume_is_never_owned_even_with_the_project_known() -> None:
    """`<project>_mongo-data` is the database this whole design exists to spare.

    Compose prefixes a stack's volumes with the project name, so knowing the
    project is what lets the per-container feature volume be recognised in a
    stack at all — and the same knowledge must not become a licence over the
    stack's declared volumes, which never appear in `mounts`.
    """
    attached = [
        {"Type": "volume", "Name": f"{_PROJECT}_{_DIND_VOLUME}"},
        {"Type": "volume", "Name": f"{_PROJECT}_mongo-data"},
        {"Type": "volume", "Name": f"{_PROJECT}_volprobe-shared-cache"},
    ]
    owned = ContainerRuntimeState.owned_volumes_in(
        declared=_DECLARED_MOUNTS, attached=attached, project=_PROJECT
    )
    assert owned == [f"{_PROJECT}_{_DIND_VOLUME}"]
    # Without the project the stack-prefixed feature volume matches nothing —
    # which is the honest answer when Grove cannot name the stack.
    assert (
        ContainerRuntimeState.owned_volumes_in(declared=_DECLARED_MOUNTS, attached=attached) == []
    )


def test_compose_teardown_removes_the_stack_then_its_own_volumes() -> None:
    """Order matters: `down` releases the references a `volume rm` would trip on."""
    docker = FakeDocker()
    minted = _compose_minted(
        observed=_compose_observed(
            mounts=({"Type": "volume", "Name": f"{_PROJECT}_{_DIND_VOLUME}"},)
        )
    )
    lifecycle, _ = _lifecycle(minted, docker=docker)
    lifecycle.teardown()

    assert docker.commands == [
        ["docker", "compose", "-p", _PROJECT, "down", "--remove-orphans"],
        ["docker", "volume", "rm", f"{_PROJECT}_{_DIND_VOLUME}"],
    ]
    assert not any("-v" in argv or "--volumes" in argv for argv in docker.commands)


def test_teardown_removes_the_per_container_volume_and_leaves_the_shared_ones() -> None:
    """End of the chain: what the mint claimed is exactly what teardown removes."""
    docker = FakeDocker()
    lifecycle, _ = _lifecycle(_minted(), docker=docker)
    lifecycle.teardown()

    assert docker.commands == [
        ["docker", "rm", "-f", FULL_ID],
        ["docker", "volume", "rm", _DIND_VOLUME],
    ]
    assert "volprobe-shared-cache" not in docker.named
    assert "volprobe-db-data" not in docker.named


def test_teardown_with_a_decoy_container_present_touches_only_ours() -> None:
    """The host runs ~50 unrelated containers; a decoy must never be named."""
    docker = FakeDocker()
    lifecycle, _ = _lifecycle(_state(owned_volumes=["grove-ws1-cache"]), docker=docker)
    lifecycle.teardown()

    assert docker.commands[0] == ["docker", "rm", "-f", FULL_ID]
    assert docker.commands[1] == ["docker", "volume", "rm", "grove-ws1-cache"]
    assert DECOY_ID not in docker.named
    # Nothing enumerates without the label filter, and nothing prunes.
    for argv in docker.commands:
        assert "prune" not in argv
        if "ps" in argv:
            assert "label=grove.managed=1" in argv


def test_label_fallback_teardown_removes_only_labelled_full_ids() -> None:
    """With no verified stack, identity comes from the labels alone.

    And that is the arm's blind spot rather than its safety: the devcontainer CLI
    id-labels the PRIMARY service only, so for a stack this reaches one container
    and leaves the siblings, the network and the volumes — which is why the state
    still says `is_compose`, so the log can say what went unreached.
    """
    docker = FakeDocker(reads={"ps": f"{FULL_ID}\n{OTHER_ID}\n"})
    unverified = _state(compose_project="foreign-stack", compose_owned=False)
    lifecycle, _ = _lifecycle(unverified, docker=docker)
    lifecycle.teardown()

    removals = [argv for argv in docker.commands if argv[:3] == ["docker", "rm", "-f"]]
    assert [argv[3] for argv in removals] == [FULL_ID, OTHER_ID]
    assert DECOY_ID not in docker.named
    # Never a project-scoped command for a stack that could not be verified.
    assert not any("compose" in argv for argv in docker.commands)
    enumerate_cmd = next(argv for argv in docker.commands if "ps" in argv)
    assert "label=grove.managed=1" in enumerate_cmd
    assert "--no-trunc" in enumerate_cmd


# ─── the verbs ───────────────────────────────────────────────────────────────


def test_pause_stops_gracefully_and_keeps_the_id() -> None:
    docker = FakeDocker()
    state = _state()
    lifecycle, _ = _lifecycle(state, docker=docker)

    assert lifecycle.pause() is None
    assert docker.commands == [["docker", "stop", "-t", "30", FULL_ID]]
    # The id is what has to survive a stop — resume reuses it.
    assert lifecycle.state.container_id == FULL_ID


def test_pause_of_a_compose_stack_is_project_scoped() -> None:
    docker = FakeDocker()
    owned = _state(compose_project="repo_devcontainer", compose_owned=True)
    lifecycle, _ = _lifecycle(owned, docker=docker)
    lifecycle.pause()
    assert docker.commands == [
        ["docker", "compose", "-p", "repo_devcontainer", "stop", "-t", "30"],
    ]


# ─── the agent shutdown ───────────────────────────────────────────────────────

#: A workspace whose agent runs under an in-container tmux — the only shape a
#: shutdown can address at all.
_IN_TMUX = "/grove/tmux/bin/amd64/tmux"
_AGENT_SESSION = "grove"


def _shutdown_exec(state: ContainerRuntimeState) -> list[str]:
    return [
        "docker",
        "exec",
        "-u",
        "vscode",
        FULL_ID,
        "sh",
        "-c",
        state.SHUTDOWN_SCRIPT,
        "grove",
        _IN_TMUX,
        _AGENT_SESSION,
        "30",
    ]


def test_pause_signals_the_agent_inside_the_namespace_before_it_stops_anything() -> None:
    """`docker stop` can never reach the agent on its own.

    Its `-t 30` is an upper bound on waiting for PID 1, which the devcontainer
    entrypoint traps and leaves at once — and the agent is not in PID 1's tree
    anyway (an exec'd process reports PPID 0), so it was never sent a signal at
    all. The exec has to come first, and it has to run as the remote user: the
    tmux socket is per-uid, so anyone else cannot even see the session.
    """
    docker = FakeDocker()
    state = _state(tmux_command=_IN_TMUX)
    lifecycle = ContainerLifecycle(state, docker=docker, agent_session=_AGENT_SESSION)

    lifecycle.pause()

    assert docker.commands == [
        _shutdown_exec(state),
        ["docker", "stop", "-t", "30", FULL_ID],
    ]


def test_kill_gives_the_agent_the_same_grace_before_rm_f() -> None:
    """`docker rm -f` is a SIGKILL with no grace — a kill is the LAST chance to flush."""
    docker = FakeDocker()
    state = _state(tmux_command=_IN_TMUX)
    lifecycle = ContainerLifecycle(state, docker=docker, agent_session=_AGENT_SESSION)

    lifecycle.teardown()

    assert docker.commands == [
        _shutdown_exec(state),
        ["docker", "rm", "-f", FULL_ID],
    ]


def test_a_compose_stack_is_signalled_through_its_own_container_id() -> None:
    """The agent lives in the workspace service, so the id form is the right one.

    A project-scoped `docker compose exec` would have to name a service, which
    is a name Grove does not mint — and the id it already recorded names the
    exact container the agent was launched into.
    """
    docker = FakeDocker()
    owned = _state(compose_project="repo_devcontainer", compose_owned=True, tmux_command=_IN_TMUX)
    ContainerLifecycle(owned, docker=docker, agent_session=_AGENT_SESSION).pause()

    assert docker.commands[0][:5] == ["docker", "exec", "-u", "vscode", FULL_ID]
    assert docker.commands[1] == [
        "docker",
        "compose",
        "-p",
        "repo_devcontainer",
        "stop",
        "-t",
        "30",
    ]


def test_a_container_with_no_in_container_tmux_says_the_guarantee_is_gone() -> None:
    """Honest degradation, not a silent one.

    A record with no in-container tmux (or an image where no tmux was
    reachable) has nothing that can NAME the agent's process from outside the
    namespace, so there is no command to emit — but the loss is real, so it is
    warned rather than skipped.
    """
    docker = FakeDocker()
    state = _state()
    assert state.tmux_command == ""
    assert state.shutdown_argv(session=_AGENT_SESSION) is None

    ContainerLifecycle(state, docker=docker, agent_session=_AGENT_SESSION).pause()
    assert docker.commands == [["docker", "stop", "-t", "30", FULL_ID]]


def test_the_shutdown_refuses_a_short_container_id_like_every_other_command() -> None:
    short = _state(container_id=FULL_ID[:12], tmux_command=_IN_TMUX)
    with pytest.raises(ContainerError, match="not a full"):
        short.shutdown_argv(session=_AGENT_SESSION)


def test_an_unreachable_container_never_blocks_reclaiming_the_worktree() -> None:
    """Best effort by contract: a failed shutdown must not abort the pause."""
    docker = FakeDocker(fail="exec")
    state = _state(tmux_command=_IN_TMUX)
    ContainerLifecycle(state, docker=docker, agent_session=_AGENT_SESSION).pause()
    assert docker.commands[-1] == ["docker", "stop", "-t", "30", FULL_ID]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux must be installed")
def test_the_shutdown_really_delivers_sigterm_and_really_waits(tmp_path: Path) -> None:
    """The producer, against a real tmux and a real `sh` — argv is not evidence.

    This is the test the original 30-second grace never had: it shipped
    documented and false because nothing ever ran it. So a trapping "agent" that
    takes 2 s to flush is started in a real tmux session and
    :attr:`ContainerRuntimeState.SHUTDOWN_SCRIPT` is run against it verbatim,
    proving both halves at once — the signal reached the agent (its marker
    exists, which `docker stop` could never produce) and the wait genuinely
    elapsed (the call took longer than the flush, rather than returning in
    0.15 s and reporting success).

    A private socket via a one-line wrapper keeps this off the developer's own
    tmux server, and passes the script a plain executable path exactly as the
    recorded `tmux_command` does.
    """
    marker = tmp_path / "flushed"
    agent = tmp_path / "agent.sh"
    agent.write_text(
        # The trap runs between foreground commands, so the loop sleeps briefly
        # rather than blocking delivery behind a long one.
        f"trap 'sleep 2; echo done > \"{marker}\"; exit 0' TERM\nwhile :; do sleep 0.2; done\n",
        encoding="utf-8",
    )
    socket = "grove-289-test"
    wrapper = tmp_path / "tmux"
    wrapper.write_text(f'#!/bin/sh\nexec tmux -L {socket} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)

    subprocess.run(
        [str(wrapper), "new-session", "-d", "-s", _AGENT_SESSION, "sh", str(agent)],
        check=True,
        capture_output=True,
    )
    try:
        started = time.monotonic()
        done = subprocess.run(
            [
                "sh",
                "-c",
                ContainerRuntimeState.SHUTDOWN_SCRIPT,
                "grove",
                str(wrapper),
                _AGENT_SESSION,
                "20",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        elapsed = time.monotonic() - started
    finally:
        subprocess.run([str(wrapper), "kill-server"], check=False, capture_output=True)

    assert done.returncode == 0, done.stderr
    assert marker.exists(), "the agent never received SIGTERM — this is the bug itself"
    assert elapsed >= 2, f"the shutdown did not wait for the agent to flush ({elapsed:.2f}s)"
    assert elapsed < 20, "it waited out the whole budget instead of noticing the exit"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux must be installed")
def test_the_shutdown_is_a_no_op_when_the_agent_session_is_already_gone(tmp_path: Path) -> None:
    """Exit status answers "did the shutdown run", never "was anything found"."""
    wrapper = tmp_path / "tmux"
    wrapper.write_text('#!/bin/sh\nexec tmux -L grove-289-absent "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    started = time.monotonic()
    done = subprocess.run(
        ["sh", "-c", ContainerRuntimeState.SHUTDOWN_SCRIPT, "grove", str(wrapper), "nope", "20"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert time.monotonic() - started < 5


def test_provisioned_is_bound_to_the_id_the_run_produced() -> None:
    errored = UpResult(outcome="error", containerId=OTHER_ID, message="postCreate failed")
    after = ContainerRuntimeState.from_up_result(errored, base=_state())
    assert after.container_id == OTHER_ID
    assert after.provisioned is False


# ─── enforcement, not documentation ──────────────────────────────────────────

#: The modules allowed to enumerate or tear down docker objects, each because it
#: owns an identity domain of its own — and each independently guaranteeing that
#: every filter it emits carries `grove.managed=1`, which is what the invariant
#: actually protects. Adding a name here is a deliberate act; the point of the
#: test is that a NEW module cannot grow an ad-hoc `docker ps --filter` and reach
#: containers Grove did not create.
#:
#: * `container_runtime.py` — one WORKSPACE's container (`grove.workspace=<id>`).
#: * `container_infra.py` — PROJECT-scoped infrastructure (`grove.scope`/
#:   `grove.project`), a vocabulary the per-workspace identity cannot express;
#:   folding the two would mean one filter builder emitting labels that are
#:   meaningless in half its call sites.
#: * `preflight.py` — `docker compose version`, a read-only capability probe
#:   that names no object at all.
_IDENTITY_OWNING_MODULES = frozenset({"container_runtime.py", "container_infra.py", "preflight.py"})
_FORBIDDEN = re.compile(r"docker\s+ps\b|[\"']compose[\"']|[\"']--filter[\"']")


def test_no_docker_ps_or_compose_invocation_outside_the_identity_owning_modules() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "grove"
    offenders = [
        path.relative_to(src).as_posix()
        for path in src.rglob("*.py")
        if path.name not in _IDENTITY_OWNING_MODULES
        and _FORBIDDEN.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "container enumeration/teardown leaked outside the allowed modules"


def test_every_infra_filter_still_carries_the_managed_label() -> None:
    """The allowlist is only safe while each member upholds the invariant itself."""
    teardown = GroveLabels.workspace_teardown_filter("ws1")
    assert "label=grove.managed=1" in teardown
    assert "label=grove.workspace=ws1" in teardown
    assert f"label=grove.scope={InfrastructureScope.WORKSPACE.value}" in teardown
