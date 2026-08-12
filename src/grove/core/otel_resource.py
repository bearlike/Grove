"""Compose the `OTEL_RESOURCE_ATTRIBUTES` env var an agent launches under.

Grove is not in the OTLP data path here — this only stamps identity onto
whatever the agent's own OTel SDK later exports, so it must work with no
endpoint, no credentials and no `telemetry.enabled`. `langfuse.session.id`
is the one non-`grove.*` key: it is the correlation attribute that lets
Grove's own spans and the agent's independently-traced spans land in the
same Langfuse *session* even though they never share a trace id.

**That key must never be absent, because an absent join key is exactly the
fragmentation it exists to prevent** — a resource attribute is frozen when
the agent's SDK starts, so a session id Grove learns later can never be
added to spans already exported under it. Where Grove can pin the harness's
OWN session id it does (`claude_code` takes a `--session-id` Grove mints, so
the agent's native spans, its transcript exporter and Grove's spine all agree
on one id). Where no such flag exists — `codex` mints its own thread id and
offers no way to supply one — the fallback is Grove's own tmux session name:
real, stable, known at launch, and already emitted as `grove.session.id`.
Falling back is not fabricating an id; it is naming the run with the one
identity that does exist at the only moment the value can be set.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from grove import __version__
from grove.core.config import AgentSpec
from grove.core.telemetry.semconv import GroveIdentityAttr, LangfuseAttr
from grove.core.workspace import WorkspaceState

ORCHESTRATOR_NAME = "grove"
"""What launched the agent. Constant because it names *this* program — the one
thing here that cannot be read from somewhere else."""


def project_name(state: WorkspaceState) -> str:
    """The project a workspace was spawned under, as one display string.

    ``<repo>`` for an ordinary workspace and ``<repo>/<subpath>`` for a nested
    one, mirroring how ``known_projects`` already distinguishes them. Public
    because both tiers of telemetry need the same answer and a second spelling
    of it is how a filter comes to match half a fleet.
    """
    repo = Path(state.repo_root).name
    return f"{repo}/{state.project_subpath}" if state.project_subpath else repo


def _encode(value: str) -> str:
    """Percent-encode a resource-attribute VALUE per the Baggage-style syntax
    `OTEL_RESOURCE_ATTRIBUTES` uses, so a title or branch containing `,`/`=`
    cannot be misread as a second key or corrupt the entries after it."""
    return quote(value, safe="")


def _parse(raw: str) -> dict[str, str]:
    """Split an existing `OTEL_RESOURCE_ATTRIBUTES` value into `key -> raw
    entry text` (kept verbatim, never decoded) so re-emitting an operator's
    own entries preserves whatever encoding they chose."""
    entries: dict[str, str] = {}
    for part in raw.split(","):
        key, sep, _ = part.strip().partition("=")
        key = key.strip()
        if sep and key:
            entries[key] = part.strip()
    return entries


def compose_resource_attributes(
    state: WorkspaceState,
    agent: AgentSpec,
    *,
    existing: str | None,
    agent_version: str | None = None,
) -> str | None:
    """The MERGED `OTEL_RESOURCE_ATTRIBUTES` value for this launch, or `None`
    if there's nothing to set.

    **An operator-set key always wins a collision.** `existing` is whatever
    the ambient env (or the agent's own `env`) already named — an operator
    who set `OTEL_RESOURCE_ATTRIBUTES` explicitly gets to override any single
    key Grove would otherwise contribute, the same "explicit wins" rule
    `_launch_env`'s outer merge already applies. Grove only fills gaps.

    A value that is absent or empty is OMITTED, never emitted as `key=`
    (which OTel readers would treat as a real, empty value rather than as
    "Grove had nothing to say"). *agent_version* is passed in rather than
    probed here — the probe is a subprocess and this function stays pure —
    and `None` for a tool with no version to report.

    **Every key Grove contributes stays under `grove.*`, including the two
    identity pairs a semantic convention also has an opinion about.** The
    agent's own SDK owns `service.*` and `telemetry.sdk.*`, and it writes them
    on the resource *inside* the exporting process, where this env var cannot
    arbitrate: a key set in both places is resolved by that SDK's own merge
    rule, not by the "operator wins" rule above. So a `service.version` here
    would be a claim about the agent that the agent itself is also making, and
    `telemetry.distro.*` would describe Grove as a distribution of an SDK it
    never touches. `grove.orchestrator.*` says the true and narrower thing —
    which program launched this run, and which build of it — and cannot
    collide with anything the agent emits.
    """
    candidates = {
        GroveIdentityAttr.WORKSPACE_ID: state.id,
        GroveIdentityAttr.WORKSPACE_TITLE: state.title,
        GroveIdentityAttr.REPO: Path(state.repo_root).name,
        # The project, which is the repo only when the workspace is not a nested
        # one. `project_subpath` is what makes several directories of one
        # checkout distinct projects sharing a worktree family, so a fleet
        # filtered by repo alone cannot tell them apart.
        GroveIdentityAttr.PROJECT: project_name(state),
        GroveIdentityAttr.BRANCH: state.branch,
        GroveIdentityAttr.BASE_BRANCH: state.base_branch,
        GroveIdentityAttr.WORKTREE: state.worktree_path,
        GroveIdentityAttr.PLACEMENT: state.placement.value,
        GroveIdentityAttr.AGENT_NAME: agent.name,
        GroveIdentityAttr.AGENT_KIND: agent.kind,
        GroveIdentityAttr.AGENT_VERSION: agent_version,
        GroveIdentityAttr.ORCHESTRATOR_NAME: ORCHESTRATOR_NAME,
        GroveIdentityAttr.ORCHESTRATOR_VERSION: __version__,
        GroveIdentityAttr.RUNTIME: state.runtime.value,
        GroveIdentityAttr.TICKET_IDS: ",".join(ref.id for ref in state.ticket_refs),
        GroveIdentityAttr.SESSION_ID: state.tmux_session,
        LangfuseAttr.SESSION_ID: state.telemetry_session_id,
    }
    merged = _parse(existing) if existing else {}
    for key, value in candidates.items():
        if value and key not in merged:
            merged[key] = f"{key}={_encode(value)}"
    if not merged:
        return None
    return ",".join(merged.values())
