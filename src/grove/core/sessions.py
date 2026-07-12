"""Cross-worktree agent-session exploration for one project.

One question: *which agent sessions belong to this project, and how do I read
one?* The :class:`SessionExplorer` aggregates what the adapters already know —
``list_sessions`` per scanned directory, ``locate_transcripts`` /
``read_turns`` per session — across the repo root, every git worktree, and
every Grove workspace path (including paused workspaces whose worktree is gone:
transcripts outlive worktrees). It annotates each session with its Grove
workspace and provenance, so the ``grove sessions`` CLI stays a thin renderer.

Read-only by construction: the explorer never launches, mutates, or deletes
anything — it composes the adapters' read-only scans. Adding a new agent tool
(codex, opencode) changes nothing here; ``all_adapters()`` picks up its
adapter automatically.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from grove.core import paths as core_paths
from grove.core.agents import (
    SessionProvenance,
    SessionSummary,
    SessionTurn,
    all_adapters,
    get_adapter,
)
from grove.core.agents.hook import ClaudeHook
from grove.core.errors import GroveError
from grove.core.git import GitRepo, detect_root
from grove.core.manager import WorkspaceManager, build

if TYPE_CHECKING:
    from grove.core.workspace import WorkspaceState

# Aware epoch for "no mtime" rows so the newest-first sort never compares
# aware and naive datetimes (that raises, and a sort must never raise here).
_EPOCH = datetime.fromtimestamp(0, tz=UTC)


@dataclass(slots=True, frozen=True)
class SessionListing:
    """One session row with its project context attached.

    ``workspace_*`` fields are ``None`` for a session found in a directory
    Grove doesn't manage (a hand-made worktree, or the repo root with no ROOT
    workspace). ``provenance`` is ``grove_launched`` only when the id matches
    a workspace's minted ``agent_session_id``.
    """

    summary: SessionSummary
    provenance: SessionProvenance
    workspace_id: str | None = None
    workspace_title: str | None = None
    workspace_branch: str | None = None


class SessionExplorer:
    """Aggregate, filter, and resolve agent sessions across a project's worktrees."""

    def __init__(self, manager: WorkspaceManager) -> None:
        self._manager = manager

    @classmethod
    def from_cwd(cls, cwd: Path) -> SessionExplorer:
        """Build an explorer for the project enclosing ``cwd``.

        Works from inside any worktree: the *main* worktree (first entry of
        ``git worktree list``) is the root the workspace store is keyed by, so
        the explorer always binds its manager there — binding to the linked
        worktree's own root would find zero workspaces.
        """
        root = detect_root(cwd)
        if root is None:
            raise GroveError(f"{cwd} is not inside a git repository")
        main_root = GitRepo(root).worktree_paths()[0]
        return cls(build(main_root))

    @property
    def repo_root(self) -> Path:
        return self._manager.repo_root

    def scan_roots(self) -> list[Path]:
        """Every directory whose sessions belong to this project, de-duplicated.

        Union of the live ``git worktree list`` (main first; covers hand-made
        worktrees Grove never managed), every workspace's persisted ``agent_cwd``
        (worktree/subpath — where a nested project's agent actually runs and
        records its transcript's cwd, #101/#118), its ``worktree_path``
        (covers paused workspaces whose directory is gone — their transcripts
        still live under the encoded-cwd projects folder), and, for a workspace
        with a ``transcript_context`` override (#147), the recorded cwd it names
        instead — a container-launched agent's real cwd, which can never equal
        either host path. ``agent_cwd`` collapses to the worktree root for the
        common empty-subpath case, so the extra entries only matter for nested
        or overridden projects.
        """
        out: list[Path] = []
        seen: set[str] = set()
        states = self._manager.list()
        candidates = [
            *GitRepo(self._manager.repo_root).worktree_paths(),
            *(state.agent_cwd for state in states),
            *(Path(state.worktree_path) for state in states),
            *(
                Path(state.transcript_context.agent_cwd)
                for state in states
                if state.transcript_context is not None
            ),
        ]
        for path in candidates:
            key = str(path)
            if key not in seen:
                seen.add(key)
                out.append(path)
        return out

    def list(
        self,
        *,
        agent: str | None = None,
        workspace: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[SessionListing]:
        """Every session across the project, newest-first, optionally filtered.

        ``agent`` matches the adapter kind exactly; ``workspace`` matches a
        workspace id prefix or a case-insensitive title substring; ``since``
        keeps sessions modified at/after that instant; ``limit`` caps the
        result after sorting.
        """
        states = self._manager.list()
        # A scanned root binds to its workspace by cwd — key on agent_cwd (a
        # nested project's real cwd, #118), worktree_path, and a
        # transcript_context override's recorded cwd (#147) so a session found
        # under any resolves to its workspace. First-wins on collision.
        by_cwd: dict[str, WorkspaceState] = {}
        for s in states:
            cwd_keys = [str(s.agent_cwd), s.worktree_path]
            if s.transcript_context is not None:
                cwd_keys.append(s.transcript_context.agent_cwd)
            for cwd_key in cwd_keys:
                by_cwd.setdefault(cwd_key, s)
        minted: dict[str, WorkspaceState] = {
            s.agent_session_id: s for s in states if s.agent_session_id
        }
        # A workspace's effective agent kind, computed once (config lookup for
        # legacy records): the cwd fallback below tags a session only when its
        # kind matches, so it's read per state, not per session.
        eff_kind: dict[str, str] = {s.id: self._manager.effective_kind(s) for s in states}
        # Root → (kind, config_dir) for a workspace's transcript_context
        # override (#147) — scopes the adapter's config-dir env var while
        # scanning that one root, so its bind-mounted host directory is what
        # `list_sessions` actually searches.
        override_by_root: dict[str, tuple[str, str]] = {
            s.transcript_context.agent_cwd: (eff_kind[s.id], s.transcript_context.config_dir)
            for s in states
            if s.transcript_context is not None
        }

        listings: list[SessionListing] = []
        seen: set[tuple[str, str]] = set()
        for root in self.scan_roots():
            override = override_by_root.get(str(root))
            scope = (
                self._manager.transcript_config_dir_scope(*override)
                if override is not None
                else contextlib.nullcontext()
            )
            with scope:
                for adapter in all_adapters():
                    for summary in adapter.list_sessions(root):
                        key = (summary.adapter_kind, summary.session_id)
                        if key in seen:
                            continue
                        seen.add(key)
                        # Minted-id equality is authoritative (Grove launched it
                        # here, and the id was minted by this workspace's own
                        # adapter, so the kind matches by construction). The cwd
                        # fallback is NOT: a ROOT workspace's cwd is the shared
                        # repo root, so a foreign-kind session there (a codex
                        # rollout under a claude_code workspace) sits in the same
                        # dir — annotating it with this workspace's id/title
                        # would mis-attribute a session the workspace can never
                        # own (its adapter can't read it; remap rejects a kind
                        # mismatch). So the cwd fallback tags only a same-kind
                        # session; otherwise the browse row stays unmapped,
                        # honest history (#164).
                        state = minted.get(summary.session_id)
                        if state is None:
                            candidate = by_cwd.get(str(root))
                            if (
                                candidate is not None
                                and summary.adapter_kind == eff_kind[candidate.id]
                            ):
                                state = candidate
                        listings.append(
                            SessionListing(
                                summary=summary,
                                provenance=(
                                    "grove_launched"
                                    if summary.session_id in minted
                                    else "fs_discovered"
                                ),
                                workspace_id=state.id if state else None,
                                workspace_title=state.title if state else None,
                                workspace_branch=state.branch if state else None,
                            )
                        )

        if agent is not None:
            listings = [ls for ls in listings if ls.summary.adapter_kind == agent]
        if workspace is not None:
            needle = workspace.lower()
            listings = [
                ls
                for ls in listings
                if (ls.workspace_id or "").startswith(workspace)
                or needle in (ls.workspace_title or "").lower()
            ]
        if since is not None:
            listings = [
                ls
                for ls in listings
                if ls.summary.modified_at is not None and ls.summary.modified_at >= since
            ]
        listings.sort(key=lambda ls: ls.summary.modified_at or _EPOCH, reverse=True)
        return listings[:limit] if limit is not None else listings

    def for_workspace(self, workspace_id: str) -> tuple[SessionListing, ...]:
        """Every session recorded for one workspace's directory, newest-first.

        The bounded variant of :meth:`list` for per-request consumers (the
        daemon's ``GET /workspaces/{id}/sessions``): scans only the workspace's
        own cwd instead of every worktree, so the transcript-parse cost stays
        one directory regardless of project size. Raises
        :class:`~grove.core.errors.WorkspaceNotFound` for an unknown id.

        Scans the union of ``state.agent_cwd`` (worktree/subpath — where a nested
        project's agent runs) and the worktree root (a session hand-started at the
        repo root records *its* cwd, #F7), deduped for a flat workspace. Both are
        where filesystem adapters exact-match a transcript's recorded cwd.

        A discovered (``fs_discovered``) listing is kept only when
        ``state.adopts_session`` accepts it — its transcript birth postdates this
        workspace's ``created_at``, OR a hook sidecar proves it was live in this
        workspace's own PANE after creation (the same pane-verified evidence rule
        `ActivityService.sessions_for` uses, so a session *resumed* in the pane —
        born before the workspace — still attributes here, while a shared cwd
        can't steal another workspace's live session, #F1). Without the gate, a
        fresh workspace whose cwd already holds older transcripts (especially
        ROOT placement, whose cwd is the shared repo root) would present a stale,
        unrelated session as its own. A ``grove_launched`` listing is never gated
        — Grove minted it for this workspace regardless of birth. :meth:`list`
        and the project-scoped listing stay ungated by design: those are
        browse-everything history views, not workspace attribution.

        Returns a tuple — in this class body a ``list[...]`` annotation would
        resolve to the :meth:`list` method, not the builtin (the documented
        mypy shadowing trap).
        """
        return self._scan_workspace(self._manager.get(workspace_id), adopt_gate=True)

    def candidates_for(self, workspace_id: str) -> tuple[SessionListing, ...]:
        """Every session recorded in one workspace's directories, newest-first,
        UNGATED — the remap-picker seam (#132).

        The ungated sibling of :meth:`for_workspace`: the same bounded one-cwd
        scan (cheap per request), but applying NO ``adopts_session`` birth/pane
        gate. So a session the auto-adoption heuristic rejects — a
        dead-minted-pointer's live successor born before the workspace, or a
        foreign session sharing a ROOT cwd — is still offered. This is exactly
        the set a human picks from to remap: the operator supplies the
        attribution the gate withholds (and `manager.remap_session`, the write
        it feeds, is likewise ungated). Provenance is still ``grove_launched``
        for the minted id, ``fs_discovered`` otherwise. Raises
        :class:`~grove.core.errors.WorkspaceNotFound` for an unknown id.

        A browse-everything counterpart to the ungated project-wide :meth:`list`,
        but scoped to one workspace's ``scan_cwds`` — so a picker pays one
        directory's parse, not the whole project's. Returns a tuple (the
        ``list``-method shadowing trap, as in :meth:`for_workspace`).
        """
        return self._scan_workspace(self._manager.get(workspace_id), adopt_gate=False)

    def _scan_workspace(
        self, state: WorkspaceState, *, adopt_gate: bool
    ) -> tuple[SessionListing, ...]:
        """The shared one-cwd scan behind :meth:`for_workspace` (``adopt_gate``
        True) and :meth:`candidates_for` (False).

        Scans the ``state.transcript_scan_cwds`` union **through the single
        adapter for this workspace's effective kind**, dedupes by ``(kind, id)``,
        tags provenance by minted-id equality, sorts newest-first by mtime. Only
        when ``adopt_gate`` does it drop a discovered listing ``ClaudeHook.adopts``
        rejects (birth ≥ ``created_at`` OR a pane-verified live-here sidecar,
        #F1) — the minted (``grove_launched``) listing is never gated either way.
        One derivation so the gated and ungated reads can't drift.

        The single-adapter restriction (#164) is the kind counterpart of the cwd
        scope: a workspace runs exactly one agent kind, so only that adapter's
        sessions can be its own. A ROOT workspace's cwd is the shared repo root,
        where the human also runs *other* tools — a foreign-kind transcript there
        (a codex rollout under a claude_code workspace) can never be this
        workspace's minted session, be adopted by its adapter, or be pinned
        (``remap_session`` rejects a kind mismatch). Scanning every adapter let
        ``candidates_for`` offer exactly those un-pinnable sessions; restricting
        to ``mgr.effective_kind`` keeps the picker in agreement with the pin and
        matches the read path (``ActivityService.sessions_for`` discovers through
        this same one adapter).

        Honors ``state.transcript_context`` (#147): ``transcript_scan_cwds``
        substitutes the container-recorded cwd for the union above, and the
        whole scan is wrapped in the matching config-dir env scope so the
        adapter searches the override's host directory instead of the ambient
        one. No override (the default) is byte-for-byte the pre-#147 scan.
        """
        kind = self._manager.effective_kind(state)
        adapter = get_adapter(kind)
        # Reference pane from the minted session's sidecar (#F1): the pane this
        # workspace owns, against which a discovered session's live-here evidence
        # is verified. Read once, before the scan loop — and only when gating.
        reference_pane: str | None = None
        if adopt_gate and state.agent_session_id:
            ref = ClaudeHook.read(
                state.agent_session_id, sidecar_dir=core_paths.agent_sidecar_dir()
            )
            reference_pane = ref.tmux_pane if ref is not None else None
        listings: list[SessionListing] = []
        seen: set[tuple[str, str]] = set()
        ctx = state.transcript_context
        with self._manager.transcript_config_dir_scope(
            kind, ctx.config_dir if ctx is not None else None
        ):
            for cwd in state.transcript_scan_cwds:
                for summary in adapter.list_sessions(cwd):
                    key = (summary.adapter_kind, summary.session_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    provenance: SessionProvenance = (
                        "grove_launched"
                        if summary.session_id == state.agent_session_id
                        else "fs_discovered"
                    )
                    if adopt_gate and provenance != "grove_launched":
                        candidate = ClaudeHook.read(
                            summary.session_id, sidecar_dir=core_paths.agent_sidecar_dir()
                        )
                        if not ClaudeHook.adopts(
                            state,
                            summary.created_at,
                            candidate=candidate,
                            reference_pane=reference_pane,
                            cwd=cwd,
                        ):
                            continue
                    listings.append(
                        SessionListing(
                            summary=summary,
                            provenance=provenance,
                            workspace_id=state.id,
                            workspace_title=state.title,
                            workspace_branch=state.branch,
                        )
                    )
        listings.sort(key=lambda ls: ls.summary.modified_at or _EPOCH, reverse=True)
        return tuple(listings)

    def resolve(self, ref: str) -> SessionListing:
        """The unique session whose id matches ``ref`` exactly or by prefix.

        Raises :class:`GroveError` when nothing matches or the prefix is
        ambiguous (the message lists the candidates, so the user can extend
        the prefix without re-running ``list``).
        """
        listings = self.list()
        exact = [ls for ls in listings if ls.summary.session_id == ref]
        if exact:
            return exact[0]
        matches = [ls for ls in listings if ls.summary.session_id.startswith(ref)]
        if not matches:
            raise GroveError(f"no session matches {ref!r} in this project")
        if len(matches) > 1:
            ids = ", ".join(ls.summary.session_id for ls in matches[:8])
            raise GroveError(f"session ref {ref!r} is ambiguous: {ids}")
        return matches[0]

    def transcripts(self, listing: SessionListing) -> tuple[Path, ...]:
        """Every transcript file for the session — main thread first, then
        sub-agent files — via the owning adapter's locator. Empty for a
        remote-backed session (no local files).

        Scoped to the owning workspace's ``transcript_context.config_dir``
        override, if any (#147) — ``_session_cwd`` already resolves to the
        session's own recorded cwd (a container path, when relevant), so only
        the adapter's config-dir env needs redirecting to find that host
        directory at all.
        """
        summary = listing.summary
        adapter = get_adapter(summary.adapter_kind)
        with self._manager.transcript_config_dir_scope(
            summary.adapter_kind, self._transcript_config_dir(listing)
        ):
            return tuple(adapter.locate_transcripts(self._session_cwd(listing), summary.session_id))

    def _transcript_config_dir(self, listing: SessionListing) -> str | None:
        """The config-dir override for reading ``listing``'s session (#147).

        ``None`` (today's behavior) when the listing isn't attributed to a
        workspace, that workspace has since vanished (a kill racing a read —
        best-effort, degrades to no override rather than raising), or it has
        no override set.
        """
        if listing.workspace_id is None:
            return None
        try:
            state = self._manager.get(listing.workspace_id)
        except GroveError:
            return None
        ctx = state.transcript_context
        return ctx.config_dir if ctx is not None else None

    def turns_for(
        self, listing: SessionListing, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        """The normalized conversation for an already-resolved listing.

        Split from :meth:`turns` so a caller holding a listing (the daemon's
        turns endpoint, fed by :meth:`for_workspace`) skips the full-project
        :meth:`resolve` scan.

        Scoped to the owning workspace's config-dir override, if any — same
        reasoning as :meth:`transcripts` (#147).
        """
        adapter = get_adapter(listing.summary.adapter_kind)
        with self._manager.transcript_config_dir_scope(
            listing.summary.adapter_kind, self._transcript_config_dir(listing)
        ):
            return adapter.read_turns(
                self._session_cwd(listing), listing.summary.session_id, last=last
            )

    def _session_cwd(self, listing: SessionListing) -> Path:
        """The cwd key the owning adapter resolves this session under.

        The recorded cwd first; a filesystem session that never recorded one
        falls back to its transcript's own folder; a remote session has
        neither, so the project root — the directory this explorer is bound
        to — is the only cwd left to ask under. One derivation for both
        :meth:`transcripts` and :meth:`turns_for` so they can't drift.
        """
        summary = listing.summary
        if summary.cwd:
            return Path(summary.cwd)
        if summary.transcript_path is not None:
            return summary.transcript_path.parent
        return self.repo_root

    def turns(self, ref: str, *, last: int | None = None) -> tuple[SessionTurn, ...]:
        """The normalized conversation for the session matching ``ref``."""
        return self.turns_for(self.resolve(ref), last=last)


__all__ = ["SessionExplorer", "SessionListing"]
