"""The in-process workspace verbs — lifecycle + read/inspect.

The lifecycle verbs (``create`` / ``message`` / ``pause`` / ``resume`` /
``respawn`` / ``kill`` / ``attach``) mirror the daemon's ``POST
/workspaces`` family over the SAME engine path the TUI uses — a direct
``WorkspaceManager`` bound to the cwd's repo via ``build()``, never an
HTTP round-trip. ``grove create`` was the headline gap in issue #45:
creation was previously reachable only through the daemon API + MCP, so a
human or an agent at a shell could not spin up a workspace.

The read verb (``show``) is the CLI analogue of the TUI peek rail: a
read-only, single-workspace, multi-perspective inspector (git + agent +
transcript + live pane) completing CLI↔TUI↔MCP read parity. It composes
the engine's existing best-effort read seams and never touches lifecycle.

Every command is registered flat on the top-level app (``grove create``,
not ``grove workspace create``) by :func:`register` in ``cli.py``,
matching the historical flatness of ``grove ls`` / ``grove version``.

The real logic here is two small atomic classes: :class:`BranchFlags`
maps the mutually-exclusive branch flags to the wire :class:`BranchPlan`
discriminated union; :class:`WorkspaceInspection` composes the read seams
into the single inspector ``show`` prints. Both are kept off the Typer
command bodies so they can be unit-tested without ``CliRunner``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import humanize
import typer

from grove.core import (
    AttachInstruction,
    AutoBranch,
    BranchPlan,
    CreateWorkspaceRequest,
    ExistingLocalBranch,
    GroveError,
    NewNamedBranch,
    RootBranch,
    SessionExplorer,
    SessionListing,
    TrackRemoteBranch,
    WorkspaceManager,
    WorkspacePeek,
    WorkspaceState,
    build,
)
from grove.core.agents import SessionTurn


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
        # No branch flag → the historical Grove default: slug the title off the base.
        return AutoBranch(base_ref=self.base or "HEAD")


# Same prompt/role glyphs the TUI transcript surfaces use (cli_sessions mirrors
# them); deliberate, not mistyped ASCII. The prompt chevron is the zsh glyph too.
_PROMPT_GLYPH = "❯"  # noqa: RUF001
_TEXT_CAP = 200  # one transcript line per row; the CLI is a glance, not a reader
_PANE_TAIL_LINES = 12  # last ~screenful of the agent's terminal
_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _truncate(text: str, cap: int = _TEXT_CAP) -> str:
    """One-line, whitespace-collapsed clip — the cli_sessions pattern, kept local
    so ``show`` doesn't import a private from a sibling module."""
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _ago(when: datetime | None) -> str:
    if when is None:
        return "-"
    return humanize.naturaldelta(datetime.now(UTC) - when) + " ago"


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

    @classmethod
    def gather(
        cls,
        manager: WorkspaceManager,
        explorer: SessionExplorer,
        workspace_id: str,
        *,
        last_turns: int = 10,
    ) -> WorkspaceInspection:
        """Compose a peek (git + pane) with the workspace's newest session + its
        recent turns. Every read is best-effort by contract — ``peek`` never
        raises, and the session tail degrades to empty rather than break the
        inspector (transcripts can be absent, mid-write, or remote-only)."""
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
        return cls(peek=peek, primary=primary, turns=turns)

    def emit(self) -> None:
        """Print the inspection in sections, each a small method over this
        value's own state. Empty sections degrade to a short note rather than a
        blank — the reader always learns *why* a perspective is missing."""
        self._emit_identity()
        self._emit_git()
        self._emit_agent()
        self._emit_transcript()
        self._emit_pane()

    def _emit_identity(self) -> None:
        state = self.peek.state
        typer.secho(f"{state.id}  {state.title}", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  branch:    {state.branch}  (base {state.base_branch})")
        typer.echo(f"  agent:     {state.agent_name}")
        typer.echo(f"  status:    {state.status.value}")
        typer.echo(f"  placement: {state.placement.value}")
        typer.echo(f"  worktree:  {state.worktree_path}")

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
        # The live ongoing-action line: the reserved #20 interpreter slot wins,
        # else the raw current-task; absent is the default (skip cleanly).
        live = a.interpreted_status or a.current_task
        if live:
            typer.echo(f"  {_truncate(live)}")

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
def _clean_exit() -> Iterator[None]:
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


def _resolve_workspace(manager: WorkspaceManager, ref: str) -> WorkspaceState:
    """The unique workspace whose id matches ``ref`` exactly or by prefix.

    Mirrors ``SessionExplorer.resolve`` — exact match wins, else a unique
    prefix; nothing / ambiguous raises :class:`GroveError` listing the
    candidates so the user can extend the prefix without re-running ``ls``.
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
    listing candidates, same currency as :func:`_resolve_workspace`."""
    if ref is not None:
        return _resolve_workspace(manager, ref)
    cwd = Path.cwd().resolve()
    # Compare RESOLVED Path objects, never raw strings (git emits '/', str(Path)
    # emits '\\' on Windows — the per-repo cross-platform rule). A ROOT workspace's
    # worktree IS the repo root, so the longest/most-specific ancestor wins when a
    # worktree nests under the root.
    best: WorkspaceState | None = None
    best_depth = -1
    for state in manager.list():
        root = Path(state.worktree_path).resolve()
        if cwd == root or root in cwd.parents:
            depth = len(root.parts)
            if depth > best_depth:
                best, best_depth = state, depth
    if best is None:
        raise GroveError("run inside a workspace worktree, or pass an id (see `grove ls`)")
    return best


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
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        "-p",
        help="The agent's first task, delivered race-free as the session boots "
        "so the workspace starts working immediately instead of idling. "
        'Example: grove create "fix login" -a claude -p "make the failing test pass".',
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
    with _clean_exit():
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
        )
        state = build().create(request)
        typer.secho(f"created {state.id}", fg=typer.colors.GREEN)
        typer.echo(f"  title:    {state.title}")
        typer.echo(f"  agent:    {state.agent_name}")
        typer.echo(f"  branch:   {state.branch}")
        typer.echo(f"  worktree: {state.worktree_path}")
        typer.echo(f"  session:  {state.tmux_session}")


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
    with _clean_exit():
        manager = build()
        state = _resolve_workspace(manager, workspace)
        manager.send_message(state.id, text)
        typer.secho(f"sent to {state.id} ({state.title})", fg=typer.colors.GREEN)


# ─── lifecycle verbs (parity with the TUI keys + the MCP tools) ───────────────
# Each is a thin shell over one WorkspaceManager method: resolve an id-prefix,
# run the op, report. The engine owns every rule (a non-running pause, a root
# pause, a missing worktree) and raises the typed error _clean_exit renders.

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
    with _clean_exit():
        manager = build()
        state = _resolve_workspace(manager, workspace)
        manager.pause(state.id, force=force)
        typer.secho(f"paused {state.id} ({state.title})", fg=typer.colors.GREEN)


def resume_workspace(workspace: str = _WORKSPACE_ARG) -> None:
    """Resume a paused workspace: recreate its worktree from the branch + relaunch.

    \b
      grove resume a1b2
    """
    with _clean_exit():
        manager = build()
        state = _resolve_workspace(manager, workspace)
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
    with _clean_exit():
        manager = build()
        state = _resolve_workspace(manager, workspace)
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
    with _clean_exit():
        manager = build()
        state = _resolve_workspace(manager, workspace)
        if not yes and not typer.confirm(f"kill {state.id} ({state.title})?"):
            raise typer.Abort()
        manager.kill(state.id, delete_branch=delete_branch)
        typer.secho(f"killed {state.id} ({state.title})", fg=typer.colors.GREEN)


def _attach_argv(instruction: AttachInstruction) -> list[str]:
    """The tmux command that hands this terminal to ``instruction``'s session.

    ``switch-client`` when already inside an outer tmux (re-point the current
    client), else ``attach`` for a standalone terminal — the same fork the core
    leaves to the client, mirrored here for the CLI. Pure, so it's unit-testable
    without exec'ing.
    """
    action = ["switch-client", "-t"] if instruction.inside_outer_tmux else ["attach", "-t"]
    return ["tmux", *action, instruction.tmux_session]


def attach_workspace(workspace: str = _WORKSPACE_ARG) -> None:
    """Attach your terminal to a workspace's tmux session (hands off to tmux).

    Resolves the id-prefix, then *replaces* this process with ``tmux attach``
    (or ``tmux switch-client`` when you're already inside tmux). The engine
    refuses a workspace with no live session — surfaced as a clean error.
    Detach with the usual tmux key (Ctrl-b d).

    \b
      grove attach a1b2
    """
    with _clean_exit():
        manager = build()
        state = _resolve_workspace(manager, workspace)
        instruction = manager.attach(state.id)
    # Outside _clean_exit: exec replaces this process, so it never returns and
    # raises no GroveError. tmux is resolved off PATH by design (Grove's one
    # hard runtime dep; a missing tmux surfaces as the OS's exec error).
    argv = _attach_argv(instruction)
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
    with _clean_exit():
        manager = build()
        state = _resolve_or_infer_workspace(manager, workspace)
        explorer = SessionExplorer.from_cwd(Path.cwd())
        WorkspaceInspection.gather(manager, explorer, state.id, last_turns=last).emit()


def register(app: typer.Typer) -> None:
    """Graft the flat workspace verbs onto the top-level app.

    Flat (not a ``workspace`` subgroup) to match ``grove ls`` / ``grove
    version``; called once from ``cli.py`` alongside the ``add_typer`` mounts.
    The set mirrors the TUI keybindings and the MCP tool surface so a human at
    a shell, an agent over MCP, and the TUI all drive the same lifecycle.
    """
    app.command("create")(create_workspace)
    app.command("message")(message_workspace)
    app.command("pause")(pause_workspace)
    app.command("resume")(resume_workspace)
    app.command("respawn")(respawn_workspace)
    app.command("kill")(kill_workspace)
    app.command("attach")(attach_workspace)
    app.command("show")(show_workspace)


__all__ = ["BranchFlags", "WorkspaceInspection", "register"]
