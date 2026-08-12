"""The in-process workspace verbs — lifecycle + read/inspect.

The lifecycle verbs (``create`` / ``message`` / ``pause`` / ``resume`` /
``respawn`` / ``kill`` / ``attach``) mirror the daemon's ``POST
/workspaces`` family over the SAME engine path the TUI uses — a direct
``WorkspaceManager`` bound to the cwd's repo via ``build()``, never an
HTTP round-trip. ``grove create`` closes a real gap: without it, creation
is reachable only through the daemon API + MCP, so a human or an agent at
a shell could not spin up a workspace.

The read verb (``show``) is the CLI analogue of the TUI peek rail: a
read-only, single-workspace, multi-perspective inspector (git + agent +
transcript + todo + live pane) completing CLI↔TUI↔MCP read parity. It
composes the engine's existing best-effort read seams and never touches
lifecycle.

The ``phase`` verb reports/reads the task-phase axis (``grove.core.phase``):
an agent-reported "how far through its task am I" claim, orthogonal to the
tmux/session status ``show`` already renders. It lives here rather than in
its own module — it is one more flat, thin-shell workspace verb, the same
shape as every other command in this file, and a dedicated ``cli_phase.py``
would only split one Typer command away from its siblings for no reader
benefit (YAGNI).

Every command is registered flat on the top-level app (``grove create``,
not ``grove workspace create``) by :func:`register` in ``cli.py``,
matching the historical flatness of ``grove ls`` / ``grove version``.

The real logic here is two small atomic classes: :class:`BranchFlags`
maps the mutually-exclusive branch flags to the wire :class:`BranchPlan`
discriminated union; :class:`WorkspaceInspection` composes the read seams
into the single inspector ``show`` prints. Both are kept off the Typer
command bodies so they can be unit-tested without ``CliRunner``.

The ``tickets`` subgroup (attach / list / detach / handover / owned /
handback) is the one exception to
the flat-verb convention above: three related verbs sharing one noun and
one ``--workspace`` option earn a small ``typer.Typer`` the same way
``grove sessions`` and ``grove config`` already do, rather than crowding
three more top-level names into the flat namespace — and ``attach`` is
unavailable as a bare top-level verb regardless, since `attach_workspace`
already owns it (tmux attach). Resolution (URL / ``#42`` / ``owner/repo#42``
→ provider + id + kind) is entirely engine-side (``WorkspaceManager.
attach_link`` / ``ticket_providers.resolve_link``) — these commands are a
thin shell, same as every other verb in this file.

``handover`` / ``owned`` / ``handback`` are the human-driven face of the
assignee work queue, and they run the SAME ``PickupEngine`` path the daemon's
background poll runs — deliberately, because a command that handed a ticket over
by a different route would claim the durable marker differently or render a
different prompt, and the divergence would only ever surface in production.
``handover`` is also the answer for "assign it to me NOW": the poll reconciles
assignments on its own cadence, this does it on the spot.

The ``fleet`` verb is a scriptable, JSON-only read of every
workspace this HOST knows about — lifecycle status, blended agent activity,
task phase, and ticket refs in one payload, so a shell orchestrator does not
have to loop ``grove show`` once per workspace to assemble what the daemon
already computes per tick. It is a new noun, not a flag on ``ls``, because it
is a genuinely different cost class: ``ls`` is a whole-file store read plus
tmux reconciliation, ``fleet`` additionally parses every workspace's
transcript and runs git ahead/behind/diff — folding that behind a flag would
put two cost profiles under one verb name, which a hypothetical
``ls --activity`` would do. It reuses
:meth:`grove.core.activity.ActivityService.snapshot` — the exact composition
the TUI dashboard, the daemon's ``GET /activity``, and the MCP
``grove_get_fleet_status`` tool already share — and serializes it through the
same :class:`~grove.core.contracts.activity.DashboardSnapshotView`, so all
four surfaces describe a fleet identically; nothing here re-derives the
blend. Host-wide (like ``grove sessions list --host``), not repo-scoped like
``ls``/``show``/``phase``: a fleet supervisor watches every project on the
host, and the MCP/daemon counterpart it must match is host-wide too.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import humanize
import typer

from grove.core import (
    ActivityService,
    AutoBranch,
    BranchPlan,
    CreateWorkspaceRequest,
    ExistingLocalBranch,
    GroveError,
    NewNamedBranch,
    RepoRegistry,
    RootBranch,
    Runtime,
    SessionExplorer,
    SessionListing,
    TrackRemoteBranch,
    WorkspaceManager,
    WorkspacePeek,
    WorkspaceState,
    build,
    load_config,
)
from grove.core.agents import SessionTurn, TodoList
from grove.core.contracts.activity import DashboardSnapshotView
from grove.core.contracts.tickets import TicketRef
from grove.core.issueops import HandoverKey, PickupEngine
from grove.core.phase import PHASE_ORDER, PhaseReport, TaskPhase, TicketClaim
from grove.core.store import JsonWorkspaceStore


@dataclass(frozen=True, slots=True)
class BranchFlags:
    """The four mutually-exclusive branch-source flags + the shared ``--base``.

    One atomic value object owning the policy "which flag becomes which
    :class:`BranchPlan` variant, and which combinations are illegal". Keeping
    it off the Typer command body lets the mapping be exercised directly in a
    unit test (no ``CliRunner``), and keeps the command a thin shell.

    Mutual exclusion: at most one of ``branch`` / ``checkout`` / ``track`` /
    ``root`` may be set. ``base`` is a modifier that only makes sense for the
    two variants that create a *new* branch off a ref (Auto, NewNamed); it is
    rejected against ``--checkout``/``--track``/``--root`` rather than silently
    ignored, so a wrong invocation fails loudly instead of doing the wrong
    thing quietly.
    """

    branch: str | None = None
    checkout: str | None = None
    track: str | None = None
    root: bool = False
    base: str | None = None

    def to_plan(self) -> BranchPlan:
        """Resolve the flags to a single :class:`BranchPlan`, or raise.

        Raises :class:`GroveError` (the CLI's clean-error currency) on any
        illegal combination so the command surfaces it the same way it
        surfaces an engine error — one message, non-zero exit.
        """
        chosen = [
            name
            for name, on in (
                ("--branch", self.branch is not None),
                ("--checkout", self.checkout is not None),
                ("--track", self.track is not None),
                ("--root", self.root),
            )
            if on
        ]
        if len(chosen) > 1:
            joined = ", ".join(chosen)
            raise GroveError(f"branch flags are mutually exclusive; got {joined}")

        base_incompatible = self.checkout is not None or self.track is not None or self.root
        if base_incompatible and self.base is not None:
            # --base only modifies a freshly-created branch (Auto / NewNamed).
            target = "--checkout" if self.checkout else "--track" if self.track else "--root"
            raise GroveError(f"--base has no effect with {target}; drop one of them")

        if self.root:
            return RootBranch()
        if self.checkout is not None:
            return ExistingLocalBranch(name=self.checkout)
        if self.track is not None:
            return TrackRemoteBranch(remote_ref=self.track)
        if self.branch is not None:
            return NewNamedBranch(name=self.branch, base_ref=self.base or "HEAD")
        # No branch flag → the default: slug the title off the base.
        return AutoBranch(base_ref=self.base or "HEAD")


# Same prompt/role glyphs the TUI transcript surfaces use (cli_sessions mirrors
# them); deliberate, not mistyped ASCII. The prompt chevron is the zsh glyph too.
_PROMPT_GLYPH = "❯"  # noqa: RUF001
_TEXT_CAP = 200  # one transcript line per row; the CLI is a glance, not a reader
_PANE_TAIL_LINES = 12  # last ~screenful of the agent's terminal
_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")
# Terminal-safe checklist glyphs — same vocabulary as the TUI's own
# CHECKBOX_MARK/ANSWERED_MARK (grove.tui._turns), redrawn locally rather than
# imported: that module builds Rich `Text` for a Textual widget, this one
# writes plain strings via `typer.echo`, so the two renderers share a
# convention, not code.
_TODO_GLYPH: dict[str, str] = {"pending": "☐", "in_progress": "▸", "completed": "✓"}

# Module-level singleton, not an inline `typer.Option(...)` default: ruff's
# B008 only exempts typer's own calls for primitive-typed params, and
# `Runtime | None` isn't one (the mcp/tools.py `_AUTO_BRANCH` precedent).
_RUNTIME_OPTION = typer.Option(
    None,
    "--runtime",
    help="Where to run the agent: host or container. Omit to use the "
    "configured default (container.enabled). Create-time only, never "
    "editable — see `grove respawn` to promote a fallback workspace.",
)

# Three-valued like `--runtime`, and a module-level singleton for the same B008
# reason: `bool | None` is not one of the primitive types ruff exempts inline.
_BRIEF_OPTION = typer.Option(
    None,
    "--brief/--no-brief",
    help="Hand the agent Grove's first-turn brief pointing it at the "
    "working-in-grove skill. Omit to use the configured default "
    "(brief.enabled). Create-time only; the choice is persisted.",
)

# Same B008 exemption gap as `_RUNTIME_OPTION` above: `TaskPhase | None` is a
# Literal union, not one of the primitive types ruff exempts inline.
_PHASE_ARGUMENT = typer.Argument(
    None,
    help="Phase to set. Omit — with or without a ref — to print the "
    "current phase instead of setting one.",
)


def _truncate(text: str, cap: int = _TEXT_CAP) -> str:
    """One-line, whitespace-collapsed clip — the cli_sessions pattern, kept local
    so ``show`` doesn't import a private from a sibling module."""
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _ago(when: datetime | None) -> str:
    if when is None:
        return "-"
    return humanize.naturaldelta(datetime.now(UTC) - when) + " ago"


def _emit_runtime_marks(state: WorkspaceState) -> None:
    """The runtime marks, different tones, shared by create + show.

    Fallback (arm 4 — runtime unavailable) is a persistent WARNING naming the
    reason + the `grove respawn` remedy; it never clears itself. Default
    container (arm 3 — no project `.devcontainer/`) is a quiet NOTICE, not a
    degradation — it names `grove init devcontainer` as the graduation path.
    Mutually exclusive by construction: a fallback workspace runs on HOST, so
    it can never also carry the default-container notice.

    No in-container tmux is a THIRD, independent mark: a container that
    came up fine but shipped no tmux binary and has no Grove-bundled fallback
    for its architecture. Independent of the other two — it can coexist with
    the default-container notice (both are container-mode facts) but never
    with the fallback warning (fallback means the workspace runs on HOST, so
    `container` is None). Also a persistent WARNING, for the same reason as
    the fallback mark: the agent dies with the terminal that launched it and
    does not survive detach, which is exactly the kind of degradation a user
    must see before they walk away, not discover after.

    The compose mark is INDEPENDENT of both and is why `is_compose` exists:
    mode is tracked separately from ownership on the record, so a stack and a
    single container render distinctly wherever a user can look, rather than
    identically. The two tones carry the consequence rather than the fact: an owned
    stack tears down whole, an unverifiable one leaves its siblings running, and
    that is the difference a user needs *before* they run `kill`, not after.
    """
    if state.runtime_fallback_reason:
        typer.secho(
            f"  ⚠ runtime fallback: {state.runtime_fallback_reason} — "
            "fix the runtime, then `grove respawn` to promote (branch + worktree preserved)",
            fg=typer.colors.YELLOW,
        )
    elif state.runtime_default_config:
        typer.echo(
            "  ⓘ default container: no project .devcontainer/ — "
            "`grove init devcontainer` to graduate to a committed config"
        )
    if state.runtime_no_tmux:
        typer.secho(
            "  ⚠ no in-container tmux: the agent dies with your terminal and "
            "does not survive detach — install tmux in the image, or Grove has "
            "no bundle for this architecture",
            fg=typer.colors.YELLOW,
        )
    _emit_compose_mark(state)


def _emit_compose_mark(state: WorkspaceState) -> None:
    """Name the compose stack this workspace runs in, and what `kill` will reach."""
    container = state.container
    if container is None or not container.is_compose:
        return
    project = container.compose_project
    if container.compose_owned:
        typer.echo(
            f"  ⓘ compose stack {project} — `kill` tears down the whole stack; "
            "its declared volumes are kept"
        )
        return
    typer.secho(
        f"  ⚠ compose stack {project} unverified — `kill` reaches this container only; "
        "sibling services, the network and any stack volumes are left on the host",
        fg=typer.colors.YELLOW,
    )


@dataclass(frozen=True, slots=True)
class WorkspaceInspection:
    """One workspace's read-only multi-perspective snapshot for `grove show`.

    Composes the engine's existing read seams (the manager's WorkspacePeek =
    git + agent pane, the SessionExplorer's transcript tail) into the single
    inspector the CLI prints — the shell analogue of the TUI peek rail. Pure
    composition: no engine logic, no lifecycle side effects.
    """

    peek: WorkspacePeek
    primary: SessionListing | None
    turns: tuple[SessionTurn, ...]
    todo: TodoList | None = None
    phase: PhaseReport | None = None
    """The task axis. Defaulted like ``todo`` so the value still constructs from
    the fields that predate it, and rendered as its own section because `show`
    is the CLI's whole-workspace inspector: it already prints the lifecycle
    status and the agent's live state, and leaving out the third axis meant the
    one status a caller SETS was the one status `show` would not report back."""

    @classmethod
    def gather(
        cls,
        manager: WorkspaceManager,
        explorer: SessionExplorer,
        workspace_id: str,
        *,
        last_turns: int = 10,
    ) -> WorkspaceInspection:
        """Compose a peek (git + pane) with the workspace's newest session, its
        recent turns, its current todo/checklist, and its reported task
        phase. Every read is best-effort by contract — ``peek`` never raises,
        and the session tail, todo, and phase all degrade to empty/None rather
        than break the inspector (transcripts can be absent, mid-write, or
        remote-only; a sessionless workspace has nothing for ``latest_todo`` to
        resolve; a workspace whose agent never reported has no phase file)."""
        peek = manager.peek(workspace_id)
        primary: SessionListing | None = None
        turns: tuple[SessionTurn, ...] = ()
        try:
            listings = explorer.for_workspace(workspace_id)
            if listings:
                primary = listings[0]  # newest-first
                turns = explorer.turns_for(primary, last=last_turns)
        except GroveError:
            # A discovery miss is "no agent session", not a command failure —
            # the inspector still prints identity + git.
            primary = None
            turns = ()
        try:
            todo = manager.latest_todo(workspace_id)
        except GroveError:
            # AgentSessionNotFound (no recorded agent session) — same "read
            # degrades, command doesn't fail" contract as the turns read above.
            todo = None
        try:
            phase = manager.phase(workspace_id)
        except GroveError:
            # `phase_for` is already best-effort about an absent/corrupt file
            # (that's a `None`, the common answer); this catches only the store
            # lookup around it, so the inspector degrades like every section.
            phase = None
        return cls(peek=peek, primary=primary, turns=turns, todo=todo, phase=phase)

    def emit(self) -> None:
        """Print the inspection in sections, each a small method over this
        value's own state. Empty sections degrade to a short note rather than a
        blank — the reader always learns *why* a perspective is missing."""
        self._emit_identity()
        self._emit_git()
        self._emit_agent()
        self._emit_todo()
        self._emit_transcript()
        self._emit_pane()

    def _emit_identity(self) -> None:
        state = self.peek.state
        typer.secho(f"{state.id}  {state.title}", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  branch:    {state.branch}  (base {state.base_branch})")
        typer.echo(f"  agent:     {state.agent_name}")
        typer.echo(f"  status:    {state.status.value}")
        # The task axis sits with the lifecycle status rather than in a section
        # of its own: both are one-line facts ABOUT the workspace, where the
        # `agent`/`todo` sections below are what the agent is doing inside it.
        typer.echo(f"  phase:     {_phase_summary(self.phase)}")
        typer.echo(f"  placement: {state.placement.value}")
        typer.echo(f"  worktree:  {state.worktree_path}")
        typer.echo(f"  runtime:   {state.runtime.value}")
        _emit_runtime_marks(state)

    def _emit_git(self) -> None:
        p = self.peek
        typer.secho("\ngit", fg=typer.colors.CYAN, bold=True)
        typer.echo(f"  ahead {p.base_ahead} · behind {p.base_behind}")
        typer.echo(f"  diff +{p.diff_added} -{p.diff_removed} · dirty {p.dirty_files}")
        if p.recent_commits:
            typer.echo("  recent commits:")
            for commit in p.recent_commits:
                subject = _truncate(commit.subject)
                typer.echo(f"    {commit.sha}  {subject}  ({_ago(commit.committed_at)})")
        else:
            typer.echo("  (no commits yet)")

    def _emit_agent(self) -> None:
        typer.secho("\nagent", fg=typer.colors.CYAN, bold=True)
        if self.primary is None:
            typer.echo("  (no agent session)")
            return
        a = self.primary.summary.activity
        typer.echo(
            f"  {a.state.value}"
            + (f" · {a.model}" if a.model else "")
            + f" · {a.human_turns} turns · {a.assistant_replies} replies"
            f" · {a.tool_calls} tools · tokens {a.tokens_in}↓ {a.tokens_out}↑"
        )
        # The live ongoing-action line: the reserved interpreter slot wins,
        # else the raw current-task; absent is the default (skip cleanly).
        live = a.interpreted_status or a.current_task
        if live:
            typer.echo(f"  {_truncate(live)}")

    def _emit_todo(self) -> None:
        typer.secho("\ntodo", fg=typer.colors.CYAN, bold=True)
        if self.todo is None or not self.todo.items:
            typer.echo("  (no todo list)")
            return
        done = sum(1 for item in self.todo.items if item.status == "completed")
        typer.echo(f"  {done}/{len(self.todo.items)} done")
        for item in self.todo.items:
            glyph = _TODO_GLYPH[item.status]
            in_progress = item.status == "in_progress"
            label = item.active_form if in_progress and item.active_form else item.content
            typer.echo(f"  {glyph} {_truncate(label)}")

    def _emit_transcript(self) -> None:
        typer.secho("\ntranscript", fg=typer.colors.CYAN, bold=True)
        if not self.turns:
            typer.echo("  (no transcript)")
            return
        for turn in self.turns:
            prompt = _truncate(turn.user_text) if turn.user_text else "(continuation)"
            typer.echo(f"  {_PROMPT_GLYPH} {prompt}")
            for entry in turn.entries:
                glyph = "⚒" if entry.role == "tool" else "⏺"
                typer.echo(f"    {glyph} {_truncate(entry.text)}")

    def _emit_pane(self) -> None:
        typer.secho("\nlive pane", fg=typer.colors.CYAN, bold=True)
        snapshot = self.peek.agent_snapshot
        if not snapshot:
            typer.echo("  (no live pane)")
            return
        typer.echo(f"  captured {_ago(self.peek.snapshot_taken_at)}")
        lines = _ANSI_SGR.sub("", snapshot).splitlines()
        for line in lines[-_PANE_TAIL_LINES:]:
            typer.echo(f"  │ {line}")


@contextmanager
def clean_exit() -> Iterator[None]:
    """Funnel any ``GroveError`` to a one-line red message + exit 1.

    The CLI's uniform error currency, wrapped around every verb body so each
    command reads as pure intent (build → resolve → run → report) with no
    repeated try/except. Engine errors — outside a repo, unknown agent, branch
    conflict, not-running, no pane — all surface identically.
    """
    try:
        yield
    except GroveError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def resolve_workspace(manager: WorkspaceManager, ref: str) -> WorkspaceState:
    """The unique workspace whose id matches ``ref`` exactly or by prefix.

    Mirrors ``SessionExplorer.resolve`` — exact match wins, else a unique
    prefix; nothing / ambiguous raises :class:`GroveError` listing the
    candidates so the user can extend the prefix without re-running ``ls``.

    Public (not ``_``-prefixed) because ``grove sessions remap`` resolves a
    workspace ref the same way — one funnel, imported cleanly rather than
    reaching across modules for a private symbol (#F10c).
    """
    states = manager.list()
    exact = [s for s in states if s.id == ref]
    if exact:
        return exact[0]
    matches = [s for s in states if s.id.startswith(ref)]
    if not matches:
        raise GroveError(f"no workspace matches {ref!r} in this repo")
    if len(matches) > 1:
        ids = ", ".join(s.id for s in matches[:8])
        raise GroveError(f"workspace ref {ref!r} is ambiguous: {ids}")
    return matches[0]


def _resolve_or_infer_workspace(manager: WorkspaceManager, ref: str | None) -> WorkspaceState:
    """The workspace named by ``ref`` (id-prefix), or — when ref is omitted —
    the one whose worktree contains the cwd. Ambiguous/none → GroveError
    listing candidates, same currency as :func:`resolve_workspace`.

    **Ambiguity is refused out loud, never resolved silently.** Ties at the
    same depth are collected, not broken by picking whichever ``manager.list()``
    happens to yield first — equal depth is not an edge case, it is exactly ROOT
    placement, where every root workspace on a repo shares the repo root as its
    worktree. Every cwd-inferring verb shares this: ``grove show``, ``grove
    phase``, ``grove tickets``. Picking one of several and reporting nothing is
    the worst available answer for all three, since the caller acts on a
    workspace it did not name; naming the candidates costs one line and leaves
    the user a ref to paste.
    """
    if ref is not None:
        return resolve_workspace(manager, ref)
    cwd = Path.cwd().resolve()
    # Compare RESOLVED Path objects, never raw strings (git emits '/', str(Path)
    # emits '\\' on Windows — the per-repo cross-platform rule). A ROOT workspace's
    # worktree IS the repo root, so the longest/most-specific ancestor wins when a
    # worktree nests under the root — and ties are collected, never broken.
    best: list[WorkspaceState] = []
    best_depth = -1
    for state in manager.list():
        root = Path(state.worktree_path).resolve()
        if cwd == root or root in cwd.parents:
            depth = len(root.parts)
            if depth > best_depth:
                best, best_depth = [state], depth
            elif depth == best_depth:
                best.append(state)
    if not best:
        raise GroveError("run inside a workspace worktree, or pass an id (see `grove ls`)")
    if len(best) > 1:
        rows = ", ".join(f"{s.id} ({s.title})" for s in best[:8])
        raise GroveError(
            f"{len(best)} workspaces share this directory — name one explicitly: {rows}"
        )
    return best[0]


def create_workspace(
    title: str = typer.Argument(
        ...,
        help="Human label for the workspace; its slug seeds the worktree path "
        "and tmux session name. Example: grove create 'fix login bug' --agent claude",
    ),
    *,
    agent: str = typer.Option(
        ...,
        "--agent",
        "-a",
        help="Agent to launch (must match a name in your config's agents list, "
        "e.g. claude). See `grove config show`.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Model id for the agent tool (e.g. claude: sonnet/opus/haiku · codex: "
        "gpt-5.5). Any id accepted; omit for the tool's default.",
    ),
    branch: str | None = typer.Option(
        None,
        "--branch",
        "-b",
        help="Create a NEW branch with this exact name off --base "
        "(e.g. --branch feature/login). Mutually exclusive with "
        "--checkout/--track/--root.",
    ),
    checkout: str | None = typer.Option(
        None,
        "--checkout",
        "-c",
        help="Check out an EXISTING local branch into the worktree "
        "(e.g. --checkout my-wip). Kept on kill (it's your branch).",
    ),
    track: str | None = typer.Option(
        None,
        "--track",
        "-t",
        help="Track a remote branch by creating a fresh local tracking branch "
        "(e.g. --track origin/feature/login).",
    ),
    root: bool = typer.Option(
        False,
        "--root",
        help="Run in the repo root on the current branch — no worktree, "
        "no new branch. The in-place escape hatch.",
    ),
    base: str | None = typer.Option(
        None,
        "--base",
        help="Git ref to base a NEW branch on (default HEAD). Only valid with "
        "the default Auto branch or --branch (e.g. --base origin/main).",
    ),
    description: str | None = typer.Option(
        None, "--description", "-d", help="Optional free-form note attached to the workspace."
    ),
    no_init: bool = typer.Option(
        False, "--no-init", help="Skip the init script for this create only."
    ),
    runtime: Runtime | None = _RUNTIME_OPTION,
    brief: bool | None = _BRIEF_OPTION,
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        "-p",
        help="The agent's first task, delivered race-free as the session boots "
        "so the workspace starts working immediately instead of idling. "
        'Example: grove create "fix login" -a claude -p "make the failing test pass".',
    ),
    resume_session: str | None = typer.Option(
        None,
        "--resume-session",
        help="Continue an EXISTING agent session in the new workspace instead of "
        "starting fresh (claude --resume / codex resume <id>). Pass a session id "
        "or a unique id prefix (see `grove sessions list`). Only claude/codex agents.",
    ),
) -> None:
    """Create a workspace and launch its agent (in-process, like the TUI).

    With no branch flag Grove auto-creates ``{branch_prefix}{slug(title)}-{ts}``
    off ``--base`` (default HEAD) — the historical default. Pick exactly one
    branch flag to override the source:

    \b
      grove create "fix login" --agent claude
      grove create "fix login" --agent claude --branch fix/login --base origin/main
      grove create "review pr" --agent claude --checkout existing-wip
      grove create "track ci"  --agent claude --track origin/feature/ci
      grove create "in place"  --agent claude --root

    Prints the new workspace id, branch, and worktree path on success;
    surfaces an engine error (unknown agent, branch conflict) with a clean
    message and a non-zero exit.
    """
    with clean_exit():
        plan = BranchFlags(
            branch=branch, checkout=checkout, track=track, root=root, base=base
        ).to_plan()
        request = CreateWorkspaceRequest(
            agent_name=agent,
            title=title,
            description=description,
            branch_plan=plan,
            skip_init=no_init,
            initial_prompt=prompt,
            resume_session_id=resume_session,
            model=model,
            runtime=runtime,
            brief=brief,
        )
        state = build().create(request)
        typer.secho(f"created {state.id}", fg=typer.colors.GREEN)
        typer.echo(f"  title:    {state.title}")
        typer.echo(f"  agent:    {state.agent_name}")
        typer.echo(f"  branch:   {state.branch}")
        typer.echo(f"  worktree: {state.worktree_path}")
        typer.echo(f"  session:  {state.tmux_session}")
        typer.echo(f"  runtime:  {state.runtime.value}")
        _emit_runtime_marks(state)


def message_workspace(
    workspace: str = typer.Argument(
        ...,
        help="Workspace id or unique id prefix (see `grove ls`). "
        "Example: grove message a1b2 'run the tests'",
    ),
    text: str = typer.Argument(..., help="The follow-up text to type into the agent and submit."),
) -> None:
    """Send a follow-up message to a running workspace's agent (steer it).

    Mirrors the TUI's steer modal and the daemon's
    ``POST /workspaces/{id}/message``: resolves the id prefix, then injects
    ``text`` into the agent pane (or re-engages a remote-backed session).
    Surfaces a clean error if the workspace isn't running or no pane resolves.

    \b
      grove message a1b2 "now add a test for the empty case"
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        manager.send_message(state.id, text)
        typer.secho(f"sent to {state.id} ({state.title})", fg=typer.colors.GREEN)


# ─── lifecycle verbs (parity with the TUI keys + the MCP tools) ───────────────
# Each is a thin shell over one WorkspaceManager method: resolve an id-prefix,
# run the op, report. The engine owns every rule (a non-running pause, a root
# pause, a missing worktree) and raises the typed error clean_exit renders.

_WORKSPACE_ARG = typer.Argument(
    ...,
    help="Workspace id or unique id prefix (see `grove ls`).",
)


def pause_workspace(
    workspace: str = _WORKSPACE_ARG,
    *,
    force: bool = typer.Option(
        False, "--force", "-f", help="Pause even with uncommitted changes in the worktree."
    ),
) -> None:
    """Pause a workspace: remove its worktree, keep the branch (resume rebuilds it).

    Refuses a dirty worktree unless ``--force`` — Grove never discards your
    uncommitted work silently.

    \b
      grove pause a1b2
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        manager.pause(state.id, force=force)
        typer.secho(f"paused {state.id} ({state.title})", fg=typer.colors.GREEN)


def resume_workspace(workspace: str = _WORKSPACE_ARG) -> None:
    """Resume a paused workspace: recreate its worktree from the branch + relaunch.

    \b
      grove resume a1b2
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        manager.resume(state.id)
        typer.secho(f"resumed {state.id} ({state.title})", fg=typer.colors.GREEN)


def respawn_workspace(workspace: str = _WORKSPACE_ARG) -> None:
    """Respawn a workspace whose tmux session vanished (worktree intact).

    Recreates the session + layout; the branch and worktree are untouched.
    Use this — not ``resume`` — when the agent's terminal died but the files
    are still on disk.

    \b
      grove respawn a1b2
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        manager.respawn(state.id)
        typer.secho(f"respawned {state.id} ({state.title})", fg=typer.colors.GREEN)


def kill_workspace(
    workspace: str = _WORKSPACE_ARG,
    *,
    delete_branch: bool | None = typer.Option(
        None,
        "--delete-branch/--keep-branch",
        help="Delete the local branch too (default: resolve from provenance — "
        "Grove-created branches are deleted, branches you attached are kept). "
        "A remote branch is never touched.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Kill a workspace: end the tmux session and remove the worktree (destructive).

    The branch is deleted only when it was Grove-created (override with
    ``--delete-branch`` / ``--keep-branch``); a remote branch is never touched.

    \b
      grove kill a1b2
      grove kill a1b2 --keep-branch -y
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        if not yes and not typer.confirm(f"kill {state.id} ({state.title})?"):
            raise typer.Abort()
        manager.kill(state.id, delete_branch=delete_branch)
        typer.secho(f"killed {state.id} ({state.title})", fg=typer.colors.GREEN)


def attach_workspace(workspace: str = _WORKSPACE_ARG) -> None:
    """Attach your terminal to a workspace's agent session (hands off to it).

    Resolves the id-prefix, then *replaces* this process with the way in the
    engine named: ``tmux attach`` / ``tmux switch-client`` for a host workspace,
    or ``devcontainer exec … tmux`` straight into the container's own tmux for a
    containerized one. The engine refuses a workspace with no live session —
    surfaced as a clean error. Detach with the usual tmux key (Ctrl-b d).

    \b
      grove attach a1b2
    """
    with clean_exit():
        manager = build()
        state = resolve_workspace(manager, workspace)
        instruction = manager.attach(state.id)
    # Outside clean_exit: exec replaces this process, so it never returns and
    # raises no GroveError. The binary is resolved off PATH by design (tmux is
    # Grove's one hard runtime dep; a missing one surfaces as the OS's exec
    # error).
    argv = instruction.terminal_argv()
    os.execvp(argv[0], argv)


# ─── read verb (parity with the TUI peek rail + the MCP read tools) ───────────


def show_workspace(
    workspace: str | None = typer.Argument(
        None, help="Workspace id / unique prefix; omit to infer from the current directory."
    ),
    *,
    last: int = typer.Option(10, "--last", "-l", help="Recent transcript turns to show."),
) -> None:
    """Inspect a single workspace (read-only): git, agent, transcript tail, live pane.

    The CLI analogue of the TUI peek rail — composes the engine's best-effort
    read seams into one multi-perspective view. With no argument it infers the
    workspace from the cwd (run it from inside a worktree); otherwise it resolves
    an id-prefix like ``grove message`` / ``grove pause``. Never mutates.

    \b
      grove show            # infer from the current worktree
      grove show a1b2 -l 20
    """
    with clean_exit():
        manager = build()
        state = _resolve_or_infer_workspace(manager, workspace)
        explorer = SessionExplorer.from_cwd(Path.cwd())
        WorkspaceInspection.gather(manager, explorer, state.id, last_turns=last).emit()


# ─── phase verb (the task-phase axis, grove.core.phase) ───────────────────────


def _phase_summary(report: PhaseReport | TicketClaim | None) -> str:
    """One phase claim as a single line — the shared formatting for the
    workspace's own claim AND every per-ticket claim underneath it, so
    `grove phase`, `grove show`'s identity block, and the per-ticket
    breakdown never hand-roll three copies of the same string.

    ``None`` renders as an explicit "(none reported)" rather than an empty
    string: an agent that has said nothing is a distinct, common answer from
    "reported scoping", and a blank would read as the latter (``PhaseFile.read``
    makes the same distinction on the engine side). ``blocked`` is a flag
    beside the phase, not a replacement — appended as a trailing note so the
    reader still sees *where it stopped* alongside *that it stopped*, mirroring
    the TUI convention (blocked is never the whole story on its own)."""
    if report is None:
        return "(none reported)"
    summary = f"{report.phase}  ({report.index + 1}/{len(PHASE_ORDER)})"
    if report.blocked:
        summary += "  (blocked)"
    return summary


def _emit_phase(state: WorkspaceState, report: PhaseReport | None) -> None:
    """Print one workspace's phase claim — the `grove show` header idiom
    (bold id + title) applied to this single-purpose command, then indented
    facts. ``report is None`` is a real, common answer (nothing reported yet),
    never an error — rendered as an honest short note, same convention as
    every degraded section in :class:`WorkspaceInspection`.

    After the workspace's own claim, list every attached ticket's own claim
    (``report.tickets``) — a workspace can report progress against several
    tickets independently, and each line reuses :func:`_phase_summary` rather
    than a second hand-rolled format. An empty ``tickets`` tuple (no per-ticket
    claims, or no report at all) prints nothing extra."""
    typer.secho(f"{state.id}  {state.title}", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  phase: {_phase_summary(report)}")
    if report is None:
        return
    if report.note:
        typer.echo(f"  note:  {report.note}")
    typer.echo(f"  age:   {_ago(report.updated_at)}")
    for claim in report.tickets:
        typer.echo(f"  ticket {claim.ticket}: {_phase_summary(claim)}")
        if claim.note:
            typer.echo(f"    note:  {claim.note}")


def phase_workspace(
    ref: str | None = typer.Argument(
        None,
        help="Workspace id/prefix. With no second argument this token may "
        "instead BE the phase itself, inferring the workspace from the cwd "
        "— the common shape for an agent reporting its own progress. Omit "
        "both arguments (or pass the literal `show`) to just print the "
        "current phase.",
    ),
    phase: TaskPhase | None = _PHASE_ARGUMENT,
    *,
    note: str | None = typer.Option(
        None, "--note", help="One-line note attached to the phase (<=200 chars)."
    ),
    ticket: str | None = typer.Option(
        None,
        "--ticket",
        help='Scope this claim to one attached ticket (its "provider:id" key) '
        "rather than the workspace as a whole — for a workspace working "
        "several attached tickets at once.",
    ),
    blocked: bool = typer.Option(
        False,
        "--blocked",
        help="Flag the phase as blocked: stuck ON this step, not just at it — "
        "a flag beside the phase, never a replacement for it.",
    ),
) -> None:
    """Report or read a workspace's task-phase: how far through its task the
    agent says it is (scoping/planning/implementing/verifying/delivering/done)
    — orthogonal to the tmux/session status `grove show` reports.

    The common caller is an agent sitting inside its own worktree, so a bare
    phase word is enough — the workspace is inferred from the cwd:

    \b
      grove phase planning                        # cwd-inferred, no note
      grove phase implementing --note "wiring the CLI verb"
      grove phase a1b2 verifying                   # explicit workspace ref
      grove phase verifying --ticket gitea:42      # scoped to one attached ticket
      grove phase implementing --blocked           # stuck on this step
      grove phase                                  # show the cwd-inferred phase
      grove phase a1b2                              # show a1b2's phase
      grove phase show                              # same as bare `grove phase`
    """
    with clean_exit():
        manager = build()
        target_phase = phase
        target_ref = ref
        # Single-token form: `grove phase <phase>` — Click hands a lone
        # positional to the FIRST argument (`ref`), so a real phase word
        # landing there means "set, cwd-inferred", not "show workspace <word>".
        if target_phase is None and ref is not None and ref != "show" and ref in PHASE_ORDER:
            target_phase = ref
            target_ref = None
        elif target_ref == "show":
            target_ref = None

        if target_phase is not None:
            state = _resolve_or_infer_workspace(manager, target_ref)
            written = manager.set_phase(
                state.id, target_phase, note, blocked=blocked, ticket=ticket
            )
            _emit_phase(state, written)
            return

        if note is not None:
            raise GroveError(
                "--note only applies when setting a phase, e.g. `grove phase planning --note ...`"
            )
        if ticket is not None:
            raise GroveError(
                "--ticket only applies when setting a phase, "
                "e.g. `grove phase planning --ticket gitea:42`"
            )
        if blocked:
            raise GroveError(
                "--blocked only applies when setting a phase, e.g. `grove phase planning --blocked`"
            )
        state = _resolve_or_infer_workspace(manager, target_ref)
        current = manager.phase(state.id)
        _emit_phase(state, current)


# ─── fleet verb (host-wide scriptable read, grove.core.activity) ──────────────


def _activity_service() -> ActivityService:
    """Host-wide :class:`ActivityService`, independent of the current cwd's project.

    Mirrors ``cli_sessions._catalog()`` (and the daemon's own construction in
    ``daemon/app.py``): ``load_config(repo_root=None)`` (user + built-in layers
    only, no single project's overlay applies) plus the shared global store.
    Same recipe, different consumer — kept local rather than shared because a
    third copy would be the actual duplication; two call sites matching a
    two-line construction is not.
    """
    cfg = load_config(repo_root=None)
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(), config_loader=load_config)
    return ActivityService(registry=registry)


def fleet_status() -> None:
    """Print every workspace this host knows about as JSON: lifecycle status,
    blended agent activity, task phase, and ticket refs — one read.

    Host-wide, like ``grove sessions list --host`` — not scoped to the current
    repo like ``grove ls``/``show``/``phase``, because the read it closes a gap
    on (``grove_get_fleet_status`` over MCP, ``GET /activity`` on the daemon) is
    host-wide too, and a shell orchestrator watching several projects wants one
    call, not one per repo. The exact wire shape ``DashboardSnapshotView``
    carries — a script parsing this output already knows the schema if it has
    ever read the daemon's activity feed or the MCP tool's response.

    \b
      grove fleet
      grove fleet | jq '.projects[].workspaces[] | select(.needs_attention)'
    """
    with clean_exit():
        snapshot = _activity_service().snapshot()
    view = DashboardSnapshotView.from_snapshot(snapshot)
    typer.echo(view.model_dump_json(indent=2))


# ─── tickets subgroup (attach / list / detach an issue or PR) ─────────────────

_TICKET_REF_ARGUMENT = typer.Argument(
    ...,
    help="Issue/PR URL, '#42', '42', or 'owner/repo#42'. No need to name the "
    "tracker (Gitea/GitHub/Linear) — inferred from the reference and the "
    "repo's enabled providers. Ambiguous across two enabled trackers? "
    "Qualify with a full URL or 'owner/repo#id'.",
)

# `-w` not `-r`/positional: a second positional would be ambiguous with `ref`
# (Typer/click has no way to tell "one token = workspace" from "one token =
# ref" when both are optional positionals — see `phase_workspace`'s manual
# disambiguation for why that trick doesn't generalize to a REQUIRED ref).
_TICKET_WORKSPACE_OPTION = typer.Option(
    None,
    "--workspace",
    "-w",
    help="Workspace id or unique id prefix. Omit to infer from the current "
    "worktree — the common shape for an agent acting on its own workspace.",
)

tickets_app = typer.Typer(
    name="tickets",
    help="Attach, list, and detach issue/PR links on a workspace.",
    no_args_is_help=True,
)


def _emit_ticket_refs(refs: Sequence[TicketRef]) -> None:
    if not refs:
        typer.echo("  (no tickets attached)")
        return
    for ref in refs:
        kind = "PR" if ref.kind == "pull_request" else "issue"
        marker = "  ⚠ ambiguous" if ref.ambiguous else ""
        typer.echo(f"  {ref.provider}#{ref.id}  ({kind}){marker}")


def tickets_attach(
    ref: str = _TICKET_REF_ARGUMENT,
    *,
    workspace: str | None = _TICKET_WORKSPACE_OPTION,
) -> None:
    """Attach an issue or pull request to a workspace — the one-call verb for
    an agent that just opened a PR and wants to link it to the workspace that
    made it. Provider and issue-vs-PR are both INFERRED from ``ref``; you never
    need to know or guess which tracker the repo uses. Idempotent — attaching
    the same ticket again, or re-attaching to correct a wrong kind, is a no-op
    / in-place fix, never a duplicate.

    \b
      grove tickets attach https://github.com/acme/api/pull/42
      grove tickets attach '#42'                    # cwd-inferred workspace
      grove tickets attach 42 --workspace a1b2
      grove tickets attach acme/api#42               # qualify when ambiguous
    """
    with clean_exit():
        manager = build()
        state = _resolve_or_infer_workspace(manager, workspace)
        updated = manager.attach_link(state.id, ref)
        typer.secho(f"attached to {state.id} ({state.title})", fg=typer.colors.GREEN)
        _emit_ticket_refs(updated.ticket_refs)


def tickets_list(
    *,
    workspace: str | None = _TICKET_WORKSPACE_OPTION,
) -> None:
    """List the issues/PRs attached to a workspace.

    \b
      grove tickets list                # cwd-inferred workspace
      grove tickets list --workspace a1b2
    """
    with clean_exit():
        manager = build()
        state = _resolve_or_infer_workspace(manager, workspace)
        typer.secho(f"{state.id}  {state.title}", fg=typer.colors.GREEN, bold=True)
        _emit_ticket_refs(state.ticket_refs)


def tickets_detach(
    ref: str = _TICKET_REF_ARGUMENT,
    *,
    workspace: str | None = _TICKET_WORKSPACE_OPTION,
) -> None:
    """Detach an issue or pull request from a workspace. Accepts the same
    reference shapes as ``grove tickets attach`` (URL / '#42' / 'owner/repo#42')
    — resolved the same way, so the ref you attached with also detaches it.
    Idempotent — detaching a ticket that was never attached is a no-op.

    \b
      grove tickets detach https://github.com/acme/api/pull/42
      grove tickets detach 42 --workspace a1b2
    """
    with clean_exit():
        manager = build()
        state = _resolve_or_infer_workspace(manager, workspace)
        selector = manager.ticket_providers.resolve_link(ref)
        updated = manager.detach_ticket(state.id, selector.provider, selector.id)
        typer.secho(
            f"detached {selector.provider}#{selector.id} from {state.id}", fg=typer.colors.GREEN
        )
        _emit_ticket_refs(updated.ticket_refs)


def _handover_key(manager: WorkspaceManager, ref: str) -> HandoverKey:
    """Resolve human-typed text to the durable key both this CLI and the poll use."""
    selector = manager.ticket_providers.resolve_link(ref)
    return PickupEngine.key_for(manager, selector.provider, selector.id)


def tickets_handover(
    ref: str = _TICKET_REF_ARGUMENT,
) -> None:
    """Hand an issue to the fleet: assign Grove's account and start a workspace on it.

    The explicit form of what assigning the bot on the tracker does implicitly.
    Records the same durable handover marker the poll uses, so the poll will not
    start a second workspace for this ticket afterwards.

    \b
      grove tickets handover 42
      grove tickets handover https://github.com/acme/api/issues/42
    """
    with clean_exit():
        manager = build()
        key = _handover_key(manager, ref)
        state = PickupEngine().hand_over(manager, key=key, source="command", now=datetime.now(UTC))
        typer.secho(f"handed {key.wire} to {state.id} ({state.title})", fg=typer.colors.GREEN)


def tickets_owned() -> None:
    """List every ticket Grove has been handed, and whether work is still live.

    Host-wide, not repo-scoped: the handover log is one file for the machine,
    and "what is my fleet holding" is a question asked from anywhere.

    \b
      grove tickets owned
    """
    with clean_exit():
        live = {s.id for s in JsonWorkspaceStore().load_all()}
        rows = PickupEngine().owned()
        if not rows:
            typer.echo("  (Grove has not been handed any tickets)")
            return
        for key, workspace_id, claimed_at in rows:
            if workspace_id is None:
                where = "no workspace started"
            elif workspace_id in live:
                where = f"workspace {workspace_id}"
            else:
                where = f"workspace {workspace_id} (gone)"
            typer.echo(f"  {key.wire}  {where}  since {claimed_at:%Y-%m-%d %H:%M}")


def tickets_handback(
    ref: str = _TICKET_REF_ARGUMENT,
) -> None:
    """Give a ticket back: unassign Grove's account, leaving any workspace alone.

    The handover marker is kept deliberately, so the poll does not immediately
    take the ticket back. Stopping the work is ``grove kill``'s job — a hand-back
    that killed a running agent would destroy uncommitted work to change a field
    on a tracker.

    \b
      grove tickets handback 42
    """
    with clean_exit():
        manager = build()
        key = _handover_key(manager, ref)
        PickupEngine().hand_back(manager, key=key, now=datetime.now(UTC))
        typer.secho(f"handed {key.wire} back", fg=typer.colors.GREEN)


tickets_app.command("attach")(tickets_attach)
tickets_app.command("list")(tickets_list)
tickets_app.command("detach")(tickets_detach)
tickets_app.command("handover")(tickets_handover)
tickets_app.command("owned")(tickets_owned)
tickets_app.command("handback")(tickets_handback)


def register(app: typer.Typer) -> None:
    """Graft the flat workspace verbs onto the top-level app.

    Flat (not a ``workspace`` subgroup) to match ``grove ls`` / ``grove
    version``; called once from ``cli.py`` alongside the ``add_typer`` mounts.
    The set mirrors the TUI keybindings and the MCP tool surface so a human at
    a shell, an agent over MCP, and the TUI all drive the same lifecycle.
    ``tickets`` is the one grafted subgroup (see the module docstring for why).
    """
    app.command("create")(create_workspace)
    app.command("message")(message_workspace)
    app.command("pause")(pause_workspace)
    app.command("resume")(resume_workspace)
    app.command("respawn")(respawn_workspace)
    app.command("kill")(kill_workspace)
    app.command("attach")(attach_workspace)
    app.command("show")(show_workspace)
    app.command("phase")(phase_workspace)
    app.command("fleet")(fleet_status)
    app.add_typer(tickets_app, name="tickets")


__all__ = ["BranchFlags", "WorkspaceInspection", "register", "resolve_workspace"]
