"""Agent and model listings for new-workspace pickers.

The TUI builds its create-modal agent dropdown straight from ``cfg.agents``
in-process; a remote client (the webapp) can't reach the config object, so this
view exposes the same merged list over the wire. Deliberately a *summary*: only
``name`` / ``kind`` / ``description`` / ``models`` cross the boundary —
``command`` and ``env`` stay daemon-side because they can carry host-private
paths and secrets, and a picker needs neither.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grove.core.config import AgentKind, AgentSpec


class ModelOptionView(BaseModel):
    """One model as a picker DRAWS it: the id, what to call it, how much it holds.

    ``AgentSummaryView.models`` is a tuple of bare ids and stays that way — it
    is a published contract with several consumers, and a picker wanting a
    second line about a model is not a reason to change what "the catalog" is.
    This is the enriched read beside it.

    ``id`` is the only field that is ever sent back to a provider. The other two
    are display, resolved from sources that may not answer: both are ``None``
    when nothing published them, never a placeholder and never a guess. A client
    renders an absent name as the id itself and an absent window as nothing at
    all — a context window reading zero on a model that holds a million tokens
    is the one claim this shape exists to make impossible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """The provider model id, verbatim — what a create or a model switch sends."""

    name: str | None = None
    """The canonical name for this id, or ``None`` when the operator declared none.

    Declared in `models.display_names` rather than derived, because the model catalogs
    Grove reads publish addresses and not names.
    """

    context_window: int | None = None
    """Input tokens this model accepts, or `None` when no configured source published one.

    Read from the same model-info snapshot the price book uses, so it costs no extra
    request. Absent for every deployment with no pricing source configured.
    """


class AgentSummaryView(BaseModel):
    """One selectable agent as a create-form client sees it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    """Identifier the client submits as ``CreateWorkspaceRequest.agent_name``."""

    kind: AgentKind
    """Which adapter introspects the agent — drives whether ``initial_prompt``
    is honored (claude_code/mewbo) or ignored (a bare ``generic`` shell)."""

    description: str = ""
    """Human label for the picker row. Empty when the agent declares none."""

    native: bool = False
    """Whether picking this agent launches a Grove-owned native session (Claude
    stream-json / Codex app-server) rather than the interactive terminal UI.
    False for every kind with no native protocol, whatever the spec says."""

    models: tuple[str, ...] = ()
    """Model ids the create-form picker OFFERS for this agent, resolved
    daemon-side by ``agents.resolve_models`` (config override, else live
    discovery). A hint, not an allowlist: the client may still submit any id as
    ``CreateWorkspaceRequest.model``. Empty when the agent exposes no catalog (a
    bare shell, or discovery found none), in which case the picker offers only a
    free-text field."""

    efforts: tuple[str, ...] = Field(default_factory=tuple)
    """Reasoning-effort levels this agent accepts as ``CreateWorkspaceRequest.effort``,
    lowest first, in the tool's own spelling. Unlike ``models`` this IS the allowlist,
    because each level becomes a launch flag the tool validates. Empty when the agent
    has no launch-time effort setting, in which case a picker offers none."""

    launch_modes: tuple[Literal["native", "terminal"], ...] = ("terminal",)
    """Which launch modes ``CreateWorkspaceRequest.native`` may pick for this entry.
    ``("native",)`` has no terminal launch, ``("terminal",)`` no native protocol."""

    runtimes: tuple[Literal["host", "container", "remote"], ...] = ("host", "container")
    """Where this entry can run. ``remote`` only for an entry that can launch
    natively, because a remote workload is reached only through a native worker
    dialling back."""

    @classmethod
    def from_spec(
        cls,
        spec: AgentSpec,
        *,
        models: Sequence[str] = (),
        efforts: Sequence[str] = (),
    ) -> AgentSummaryView:
        """Serialize one ``AgentSpec``.

        Args:
            spec: The configured agent.
            models: The per-agent catalog the daemon already resolved, kept out
                of this pure view so it never runs the Codex subprocess itself.
            efforts: The agent kind's effort levels, read off its adapter.

        Returns:
            The wire summary. Both lists default to empty.
        """
        modes = spec.launch_modes
        runtimes: tuple[Literal["host", "container", "remote"], ...] = (
            ("host", "container", "remote") if "native" in modes else ("host", "container")
        )
        return cls(
            name=spec.name,
            kind=spec.kind,
            description=spec.description,
            native=spec.owns_native_session,
            models=tuple(models),
            efforts=tuple(efforts),
            launch_modes=modes,
            runtimes=runtimes,
        )


__all__ = ["AgentSummaryView"]
