"""Build the canonical demo fleet on disk from the declared world.

The primary repo is the one the TUI opens on; its top (most recent) workspace is
a live session mid-task, so the peek rail, the steer modal and the sessions
browser all render against real content. The secondary repo gives the project
switcher and the dashboard a second repo to show. Both carry a Codex workspace,
so the Codex vendor mark is visible in the flat sidebar list. One workspace is
taken OFFLINE by killing its tmux session, so the status palette is visible at a
glance.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.screenshots.fixture import DemoWorkspace, DemoWorld

from grove.core import tmux as tmux_mod
from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import NewNamedBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.tickets import TicketRef
from grove.core.manager import WorkspaceManager
from grove.core.phase import PhaseFile
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState

from .claude import ClaudeTranscriptPlanter
from .codex import CodexRolloutPlanter
from .config import DemoConfig
from .history import HistoryPlanter
from .repo import GitTree
from .tmux import DemoTmux
from .usage import UsageCacheWarmer


@dataclass(slots=True)
class Fleet:
    """The seeded demo fleet handed back to a capture tool."""

    primary: WorkspaceManager
    secondary: WorkspaceManager
    store: JsonWorkspaceStore

    def managers(self) -> tuple[WorkspaceManager, WorkspaceManager]:
        return self.primary, self.secondary

    def teardown(self) -> None:
        DemoTmux.teardown(*self.managers())


class FleetPlanter:
    """Turns a `DemoWorld` into real repos, worktrees, sessions and transcripts.

    Collaborators are injected rather than resolved here: a caller supplies the
    store (the TUI capture seeds into a non-default path) and, through it, the
    config every manager is built from.
    """

    SETTLE_SECONDS = 3
    """Long enough for the stub agents to start, print, and reach `sleep`, so
    `capture-pane` returns the printed content rather than an empty pane."""

    def __init__(self, world: DemoWorld, *, store: JsonWorkspaceStore) -> None:
        self._world = world
        self._store = store
        self._config = DemoConfig(world)
        self._cfg: GroveConfig = self._config.resolve()
        self._claude = ClaudeTranscriptPlanter.from_env(tempo=world.tempo)
        self._codex = CodexRolloutPlanter.from_env(tempo=world.tempo)

    def manager(self, repo: Path) -> WorkspaceManager:
        """A real-tmux manager bound to ``repo``, sharing the store with peers."""
        return WorkspaceManager(repo_root=repo, cfg=self._cfg, store=self._store)

    def plant(self, work: Path) -> Fleet:
        repos = {name: GitTree.init_repo(work, name).path for name in self._world.repos.names}
        managers = {name: self.manager(path) for name, path in repos.items()}

        created = [
            (self._create(managers[entry.repo], entry), entry) for entry in self._world.workspaces
        ]
        self._describe(created)

        # Backdated, non-workspace history for the usage page. Planted BEFORE
        # the live sessions, and the order is load-bearing: `CodexQuotaProvider`
        # reads the newest rollout files by mtime, so whichever rollout is
        # written last is the one the subscription card reports from — and only
        # a live session's windows have a reset in the future. Planted the other
        # way round, the card reads `stale`.
        HistoryPlanter(
            corpus=self._world.history,
            tempo=self._world.tempo,
            claude=self._claude,
            codex=self._codex,
        ).plant(work)

        self._git_shape(created)
        self._transcripts(created)
        self._phases(created)

        self._config.publish()
        UsageCacheWarmer(cfg=self._cfg, store=self._store).warm()
        time.sleep(self.SETTLE_SECONDS)
        self._take_offline(created)

        return Fleet(
            primary=managers[self._world.repos.primary],
            secondary=managers[self._world.repos.secondary],
            store=self._store,
        )

    def _create(self, manager: WorkspaceManager, entry: DemoWorkspace) -> WorkspaceState:
        return manager.create(
            CreateWorkspaceRequest(
                agent_name=entry.agent,
                title=entry.title,
                branch_plan=NewNamedBranch(name=entry.branch),
            )
        )

    def _describe(self, created: list[tuple[WorkspaceState, DemoWorkspace]]) -> None:
        """Give every workspace its description and attached tickets.

        Ticket refs land pre-enriched (title/url/status/assignee) rather than
        through `attach_ticket` (which stores the bare selector only) — the
        daemon's own on-demand fetch is what fills those fields for real, and
        there is no ticket provider configured here to fetch from.
        """
        for state, entry in created:
            self._store.save(
                replace(
                    state,
                    description=entry.description,
                    ticket_refs=[TicketRef(**ticket.model_dump()) for ticket in entry.tickets],
                )
            )

    @staticmethod
    def _git_shape(created: list[tuple[WorkspaceState, DemoWorkspace]]) -> None:
        for state, entry in created:
            tree = GitTree(path=Path(state.worktree_path))
            if entry.working:
                tree.leave_dirty()
            if entry.ahead:
                tree.commit_ahead(entry.title)

    def _transcripts(self, created: list[tuple[WorkspaceState, DemoWorkspace]]) -> None:
        """Plant every Claude and Codex session's recorded turns.

        Codex mints no session id of its own (see
        ``src/grove/core/agents/codex.py``), so Grove finds it purely through
        discovery — the rollout's birth just has to postdate `created_at`, which
        a fresh `datetime.now()` plus a small safety margin guarantees.
        """
        for state, entry in created:
            if entry.transcript is None:
                continue
            if state.agent_kind == "claude_code" and state.agent_session_id:
                self._claude.plant(
                    cwd=Path(state.worktree_path),
                    session_id=state.agent_session_id,
                    transcript=entry.transcript,
                    model=self._world.models["claude_code"],
                    base=self._world.base_time,
                )
            elif state.agent_kind == "codex":
                self._codex.plant(
                    cwd=Path(state.worktree_path),
                    session_id=str(uuid.uuid4()),
                    branch=state.branch,
                    transcript=entry.transcript,
                    model=self._world.models["codex"],
                    base=datetime.now(tz=UTC) + timedelta(seconds=2),
                )

    @staticmethod
    def _phases(created: list[tuple[WorkspaceState, DemoWorkspace]]) -> None:
        """Write each workspace's task-phase claim, plus any per-ticket claims.

        Stands in for the agent that would otherwise write this file itself (see
        `grove.core.phase` and the `working-in-grove` skill). The
        workspace-level claim is written first — a per-ticket write leaves the
        held workspace phase alone, so order here only matters for readability.
        """
        for state, entry in created:
            worktree = Path(state.worktree_path)
            key = PhaseFile.key_for(state.id)
            PhaseFile.write(worktree, key, entry.phase.phase, entry.phase.note)
            for claim in entry.phase.tickets:
                PhaseFile.write(worktree, key, claim.phase, claim.note, ticket=claim.ticket)

    @staticmethod
    def _take_offline(created: list[tuple[WorkspaceState, DemoWorkspace]]) -> None:
        """Kill the marked session out from under the reconciler, so the list
        shows an offline row and the respawn key."""
        for state, entry in created:
            if entry.offline:
                tmux_mod.kill_session(state.tmux_session)
