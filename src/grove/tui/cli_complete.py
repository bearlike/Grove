"""Shell completion for the Grove CLI — the candidate values, and the script
that delivers them.

Two halves of one question ("how does a shell complete ``grove``?"), so one
module: :class:`Complete` supplies the values a parameter can take, and
:class:`CompletionScript` writes the tiny shell function that calls back into
Grove to ask.

Three rules govern the completers, and each exists because breaking it produces
a failure the author of a completer does not feel.

**A completer never enumerates anything itself.** It calls the same engine seam
the command's own body calls — the config cascade for agents, ``resolve_models``
for models, the workspace store for ids, ``GitRepo`` for branches. So a new
agent in a project config, a curated ``AgentSpec.models`` list, or a renamed
working directory shows up at the next TAB with no edit here. A completer
holding its own list would be a second source of truth for the one thing this
module exists to reflect, and it would drift in silence: nothing fails when a
completion offers a stale value, it just quietly stops matching the config.

**A completer never reconciles, and the obvious call is almost always the
expensive one** — because the surfaces this CLI already has want *reconciled*
state, so the seam named after the domain is the one built for a screen, not a
keystroke. Measured on the reference host: ``manager.store.for_repo()`` is
0.2 ms while ``manager.list()`` shells out to tmux (and ``docker`` per container
workspace) per row; ``AgentAdapter.discover_sessions()`` over the project's scan
roots is 111 ms for 68 ids while ``SessionExplorer.list()`` — what ``grove
sessions list`` itself calls — is **14.6 s**, since its ``limit`` caps after
sorting and it fully parses every transcript in the project first. Read the
store, the config and git; never the runtime.

**A completer never raises.** :func:`_candidates` swallows everything, because
the caller is a shell mid-TAB: a traceback there lands in the user's command
line, and one exception completing ``--model`` breaks completion for the whole
command. An empty list degrades to Typer's ``_files``, which is exactly what
the parameter had before this module existed.

Typer converts a ``(value, help)`` tuple into a ``CompletionItem`` and — this is
why nothing here filters — **applies the ``startswith(incomplete)`` filter
itself** (``typer.core._typer_param_setup_autocompletion_compat``). A completer
returns its whole domain and lets that one implementation narrow it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import click
import typer

from grove.core.errors import GroveError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

    from grove.core.manager import WorkspaceManager

# A shell redraws the line after every TAB, so an unbounded list is a hostile
# answer as well as a slow one. Generous on purpose: it bounds the pathological
# repo (hundreds of abandoned branches), never an ordinary one.
_MAX_CANDIDATES = 250

# Wide enough to say something useful, narrow enough that a normal terminal
# never re-wraps a row — see `_help` for why a wrap is not merely ugly here.
_HELP_CAP = 60

# `zsh -i` sources the user's rc, which is the only way to see an fpath entry
# their .zshrc adds. Bounded because an rc can do anything, including block.
_FPATH_PROBE_TIMEOUT = 15


def _help(text: str) -> str:
    """One short, single-line hint — the only shape a completion help may take.

    **A newline in a description silently breaks the whole completion.** Typer
    renders help through Rich before emitting it, and Rich WRAPS to console
    width, so a description merely longer than the terminal comes back with
    newlines embedded — which land inside the ``_arguments '*: :((…))'`` spec,
    where a newline separates entries. zsh then parses the tail of one
    description as a candidate and the menu never appears.

    It fails in the worst possible way: the round trip prints plausible-looking
    output, ``zsh -n`` parses it happily (a newline inside quotes is valid
    syntax; the damage is semantic), and short descriptions work fine — so it
    presents as "completion works for some flags and not others". Found by
    pressing TAB on ``--agent``, whose configured descriptions are long.

    Collapsing whitespace is the fix; the cap is what keeps the menu readable
    and stops any one row from being re-wrapped by a narrow terminal.
    """
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= _HELP_CAP else collapsed[: _HELP_CAP - 1].rstrip() + "…"


def _mute_logging() -> None:
    """Drop every loguru sink for the rest of this process.

    A completion is not a normal invocation: nothing calls the Typer callback
    (Click resolves the command with ``resilient_parsing``), so
    ``_configure_logging`` never runs and loguru keeps its DEFAULT sink — stderr
    at DEBUG. The engine is chatty at that level, so a completer's own
    ``build()`` prints its git and config-cascade lines straight into the user's
    terminal on **every TAB**.

    That is invisible from the shell's side and easy to wave away, which is why
    it survived a first review here: ``eval $(...)`` captures stdout only, so the
    completion still WORKS — the lines simply land on the terminal, over the
    prompt. Verified by pressing TAB in a real interactive zsh; nothing short of
    that shows it.

    Safe as a global mutation because the only callers are the completers, and a
    process running one does nothing else.
    """
    from loguru import logger  # noqa: PLC0415 - import cost is per-TAB latency

    logger.remove()


def _candidates(produce: Callable[[], list[tuple[str, str]]]) -> list[tuple[str, str]]:
    """Run ``produce``, degrading any failure to "no candidates".

    Deliberately catches :class:`Exception` rather than :class:`GroveError`. The
    engine's typed errors are only the expected half — a completer also runs
    outside a git repo, against a half-written config, against a store another
    process is rewriting, and against agent binaries that may not exist. None of
    those deserve a traceback in somebody's shell, and every one of them is
    correctly answered by offering nothing.
    """
    _mute_logging()
    try:
        rows = produce()[:_MAX_CANDIDATES]
    except Exception:  # a completion must never break a shell
        return []
    # Normalised HERE rather than at each call site, so no completer can forget
    # and break the menu for every other one on the same parameter.
    return [(value, _help(text)) for value, text in rows]


def _manager() -> WorkspaceManager | None:
    """A manager bound to the cwd's repo, or ``None`` outside one.

    ``build()`` costs 5 ms on the reference host — two ``git`` reads plus the
    config cascade — so completers reuse it rather than re-deriving a repo root.
    That also keeps them correct by construction: ``build()`` resolves the *main*
    worktree root the workspace store is keyed by, which is precisely what a
    hand-rolled ``detect_root`` here would get wrong from inside a linked
    worktree, offering zero workspaces with nothing to explain why.
    """
    from grove.core import build  # noqa: PLC0415 - import cost is per-TAB latency

    try:
        return build()
    except Exception:  # not in a repo, or unreadable config
        return None


class Complete:
    """The completion callbacks, one per value domain the CLI accepts.

    Static methods on a class rather than free functions: the set is one
    vocabulary, and a wiring site then reads ``Complete.workspaces`` — naming the
    domain rather than a module-private helper.

    Every method takes ``incomplete`` even where it ignores it, because that is
    the signature Typer introspects: it assigns parameters by ANNOTATION
    (``click.Context`` → ctx, ``str`` → the partial word), not by position.
    Typer always passes an empty ``args`` list, so no method takes one.

    **``click`` is imported at module scope, and must stay that way.** This
    module uses ``from __future__ import annotations``, so those annotations are
    strings — and Typer RESOLVES them at runtime (``get_params_from_function``
    → ``inspect.signature(eval_str=True)``) to decide which parameter is the
    context. Under ``TYPE_CHECKING`` the name is absent at that moment and every
    ctx-taking completer dies with ``NameError: name 'click' is not defined``.
    Nothing static catches it: ruff, mypy and importing the module are all
    happy, because the failure needs a real completion to run.
    """

    @staticmethod
    def workspaces(incomplete: str) -> list[tuple[str, str]]:
        """Workspace ids in the cwd's repo, helped by title and branch.

        Reads the STORE, never ``manager.list()``: id, title and branch are
        persisted facts, and the reconciled status a listing would add costs a
        ``tmux has-session`` per workspace — plus a ``docker inspect`` per
        container one — to decorate a value the user is already typing.
        """
        del incomplete  # Typer filters by prefix; see the module docstring.

        def produce() -> list[tuple[str, str]]:
            manager = _manager()
            if manager is None:
                return []
            return [
                (state.id, f"{state.title} · {state.branch}")
                for state in manager.store.for_repo(manager.repo_root)
            ]

        return _candidates(produce)

    @staticmethod
    def agents(incomplete: str) -> list[tuple[str, str]]:
        """Agent names from the resolved config cascade.

        The roster is exactly what ``grove create --agent`` validates against, so
        this can never offer a name the create would then refuse.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            manager = _manager()
            if manager is None:
                return []
            return [(spec.name, spec.description or spec.command) for spec in manager.config.agents]

        return _candidates(produce)

    @staticmethod
    def models(ctx: click.Context, incomplete: str) -> list[tuple[str, str]]:
        """Model ids for the agent named earlier on the same line.

        The one context-sensitive completer. Click parses already-typed flags
        into ``ctx.params`` before asking for completions, so ``grove create
        --agent codex --model <TAB>`` offers Codex's catalog rather than every
        agent's. With no ``--agent`` yet the union across the roster is offered,
        each row helped by the agent it came from — a create with no agent flag
        resolves one from config too, so claiming there is no answer would be
        less useful than a labelled union.

        Never an allowlist. ``resolve_models`` is a display seam and create
        forwards any id verbatim (the provider boundary), so an id absent here
        stays valid: completion narrows typing, not the parameter.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            from grove.core.agents.registry import resolve_models  # noqa: PLC0415

            manager = _manager()
            if manager is None:
                return []
            named = ctx.params.get("agent")
            specs = [s for s in manager.config.agents if not named or s.name == named]
            rows: list[tuple[str, str]] = []
            seen: set[str] = set()
            for spec in specs:
                for model in resolve_models(
                    kind=spec.kind, command=spec.command, configured=spec.models
                ):
                    if model not in seen:
                        seen.add(model)
                        rows.append((model, spec.name))
            return rows

        return _candidates(produce)

    @staticmethod
    def local_branches(incomplete: str) -> list[tuple[str, str]]:
        """Local branches, helped by where each one is already checked out.

        That help is the point rather than decoration: ``--checkout`` refuses a
        branch another worktree holds (``BranchAlreadyCheckedOut``), so naming
        the holder turns a create that would have failed into one the user
        redirects before pressing enter.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            manager = _manager()
            if manager is None:
                return []
            rows: list[tuple[str, str]] = []
            for branch in manager.list_local_branches():
                if branch.checked_out_in is not None:
                    note = f"checked out in {branch.checked_out_in.name}"
                elif branch.is_current:
                    note = "current"
                else:
                    note = branch.upstream or "local"
                rows.append((branch.name, note))
            return rows

        return _candidates(produce)

    @staticmethod
    def remote_branches(incomplete: str) -> list[tuple[str, str]]:
        """Remote-tracking branches, the domain of ``--track``."""
        del incomplete

        def produce() -> list[tuple[str, str]]:
            manager = _manager()
            if manager is None:
                return []
            return [(b.name, "remote") for b in manager.list_remote_branches()]

        return _candidates(produce)

    @staticmethod
    def refs(incomplete: str) -> list[tuple[str, str]]:
        """Every branch, local and remote — the domain of ``--base``.

        A base is any start point, so both halves qualify. Locals lead because
        they are what somebody naming a base usually means.
        """
        return _candidates(
            lambda: Complete.local_branches(incomplete) + Complete.remote_branches(incomplete)
        )

    @staticmethod
    def cwds(incomplete: str) -> list[tuple[str, str]]:
        """The repo's labelled working directories, for ``--cwd``.

        Offers the PATH with the label as help, because the path is what reaches
        the wire — a label is a presentation concern the engine never learns, so
        completing one would offer a value ``--cwd`` cannot accept.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            manager = _manager()
            if manager is None:
                return []
            cwds = manager.config.agent_cwds
            return [
                (path, f"{label} (default)" if label == cwds.default else label)
                for label, path in cwds.entries.items()
            ]

        return _candidates(produce)

    @staticmethod
    def sessions(incomplete: str) -> list[tuple[str, str]]:
        """Agent session ids recorded anywhere in this project.

        Uses ``AgentAdapter.discover_sessions`` — the per-cwd scan the activity
        poll already runs at ~0.5 Hz, cheap by contract — across the explorer's
        scan roots. Emphatically not ``SessionExplorer.list()``, whose ``limit``
        applies only after a full parse of every transcript in the project:
        14.6 s against 111 ms here, for a value the user is mid-keystroke on.

        The price of the cheap seam is that a row carries no title or timestamp,
        so the help is the adapter kind. That is the honest trade — these are
        UUIDs nobody types from memory, which is the whole reason to complete
        them, and ``grove sessions list`` remains where the richer answer lives.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            from grove.core import SessionExplorer  # noqa: PLC0415
            from grove.core.agents.registry import all_adapters  # noqa: PLC0415

            roots = SessionExplorer.from_cwd(Path.cwd()).scan_roots()
            rows: list[tuple[str, str]] = []
            seen: set[str] = set()
            for adapter in all_adapters():
                for root in roots:
                    for session_id in adapter.discover_sessions(root):
                        if session_id not in seen:
                            seen.add(session_id)
                            rows.append((session_id, adapter.kind))
            return rows

        return _candidates(produce)

    @staticmethod
    def tickets(ctx: click.Context, incomplete: str) -> list[tuple[str, str]]:
        """Ticket refs already attached to the workspace in play.

        Scoped to attached refs because the commands taking a completable ticket
        — ``tickets detach``, ``phase --ticket`` — act on something the workspace
        already carries. ``tickets attach`` deliberately gets no completer: its
        domain is every issue on a remote tracker, which is a network round trip
        per TAB.

        Resolves the workspace exactly as the verbs do, so a ``--workspace``
        typed earlier is honoured and an omitted one falls back to the cwd.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            from grove.tui.cli_workspace import resolve_or_infer_workspace  # noqa: PLC0415

            manager = _manager()
            if manager is None:
                return []
            ref = ctx.params.get("workspace")
            state = resolve_or_infer_workspace(manager, ref if isinstance(ref, str) else None)
            return [(t.key, t.kind) for t in state.ticket_refs]

        return _candidates(produce)

    @staticmethod
    def adapter_kinds(incomplete: str) -> list[tuple[str, str]]:
        """Agent ADAPTER kinds, for ``sessions list --agent``.

        A different domain from :meth:`agents` despite the flag's name: that
        filter matches ``SessionSummary.adapter_kind`` (``claude_code``,
        ``codex``, …), not a configured agent's name, so completing the roster
        here would offer values that silently match nothing.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            from grove.core.agents.registry import all_adapters  # noqa: PLC0415

            return [(adapter.kind, "adapter kind") for adapter in all_adapters()]

        return _candidates(produce)

    @staticmethod
    def workspaces_or_phases(incomplete: str) -> list[tuple[str, str]]:
        """Both domains ``grove phase``'s first argument actually accepts.

        That argument is a workspace ref *or* — with no second argument — the
        phase itself, inferring the workspace from the cwd. Completing only
        workspace ids would hide the shape the verb is used in most, since an
        agent reporting its own progress types ``grove phase implementing``.
        Phases lead, because that is the common form.
        """
        from grove.core.phase import PHASE_ORDER  # noqa: PLC0415

        phases = [(phase, "task phase") for phase in PHASE_ORDER]
        return _candidates(lambda: phases + Complete.workspaces(incomplete))

    @staticmethod
    def pairing_challenges(incomplete: str) -> list[tuple[str, str]]:
        """Pending pairing challenge ids, for ``auth approve`` / ``auth deny``.

        Host-wide, like the verbs themselves: a pairing request belongs to the
        machine rather than to a repo, so this reads the auth store directly and
        never resolves a project.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            from grove.core.auth import SessionStore  # noqa: PLC0415

            return [
                (str(c.challenge_id), f"{c.label} · {c.state.value}")
                for c in SessionStore().list_pending_challenges()
            ]

        return _candidates(produce)

    @staticmethod
    def auth_sessions(incomplete: str) -> list[tuple[str, str]]:
        """Active session ids, for ``auth revoke``.

        Revoked sessions are already hidden by the store, so everything offered
        here is something ``revoke`` can actually act on.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            from grove.core.auth import SessionStore  # noqa: PLC0415

            return [(str(s.session_id), s.label) for s in SessionStore().list_sessions()]

        return _candidates(produce)

    @staticmethod
    def container_agent_names(ctx: click.Context, incomplete: str) -> list[tuple[str, str]]:
        """Extra agent names live inside a containerized workspace.

        The one completer that must touch a runtime, because an in-container
        agent exists nowhere else: it is built from what the container's tmux
        server reports, never from a stored list, so there is no cheap record to
        read instead. Offered anyway because ``grove agent
        kill|peek|message|attach`` are unusable without it, and bounded by the
        same guard as everything here — a stopped container or an absent
        ``docker`` yields no candidates rather than a hang nobody can cancel.
        """
        del incomplete

        def produce() -> list[tuple[str, str]]:
            from grove.tui.cli_workspace import resolve_workspace  # noqa: PLC0415

            manager = _manager()
            if manager is None:
                return []
            ref = ctx.params.get("workspace")
            if not isinstance(ref, str) or not ref:
                return []
            state = resolve_workspace(manager, ref)
            return [
                (agent.name, "primary" if agent.primary else "extra")
                for agent in manager.container_agents(state.id)
            ]

        return _candidates(produce)


class CompletionScript:
    """The per-shell completion script, and where a shell will look for it.

    Grove renders nothing itself — :meth:`render` asks Click for the class Typer
    registered and returns its ``source()``, so the script has exactly one
    definition and it is the one Typer's own ``--show-completion`` prints. What
    Grove owns is only the *install location*, which is where Typer's installer
    is wrong for a configured shell.

    **Install writes one file into a directory the shell already searches, and
    never edits an rc file.** That single rule covers zsh, bash and fish, and it
    covers Linux and macOS without an OS branch, because the question it asks —
    "where does this shell look?" — is answered by the shell rather than by a
    table of conventions that is wrong on somebody's machine. Typer's installer
    instead appends ``fpath+=~/.zfunc; autoload -Uz compinit; compinit`` to the
    END of ``~/.zshrc``: under oh-my-zsh that lands *after* ``oh-my-zsh.sh`` has
    already run ``compinit``, so every shell pays a second full ``compinit``, and
    it ignores whatever completion directory the user already curates. Editing
    somebody's shell rc is also the one step here that is hard to undo, which is
    reason enough to print a line instead of writing one.
    """

    #: Where each shell looks with no rc change at all, RELATIVE TO HOME. zsh is
    #: absent because it has no single answer — its search path is ``$fpath``,
    #: which the user's own rc composes, so :meth:`zsh_dirs` asks the shell.
    #:
    #: Home-relative rather than ``~``-prefixed on purpose: ``Path.expanduser``
    #: resolves ``~`` from ``$HOME`` while :meth:`zsh_target` uses
    #: ``Path.home()``, and a module holding two notions of "home" resolves them
    #: differently the moment anything (a test, ``sudo -E``, a login shell with a
    #: stale ``HOME``) makes them disagree. One source, joined at use.
    CONVENTIONAL_DIRS: ClassVar[dict[str, tuple[str, str]]] = {
        # shell: (home-relative directory, filename)
        "bash": (".local/share/bash-completion/completions", "grove"),
        "fish": (".config/fish/completions", "grove.fish"),
    }

    ZSH_FILENAME = "_grove"

    #: Used only when the shell reports no writable user directory on ``$fpath``.
    #: Chosen to match the ``site-functions`` convention rather than Typer's
    #: ``~/.zfunc``, and never created silently — the caller is told the one
    #: ``fpath`` line to add, because a directory the shell does not search is
    #: an install that looks like it worked and does nothing.
    ZSH_FALLBACK_DIR = ".local/share/zsh/site-functions"

    #: Appended to Typer's zsh script, because Typer's template assumes the file
    #: is SOURCED and Grove installs it on ``$fpath`` to be AUTOLOADED — two
    #: different contracts, and the mismatch costs the first TAB of every shell.
    #:
    #: Autoloaded via the ``#compdef`` tag, the file's body runs *as* the
    #: completion. Typer's body only defines ``_grove_completion`` and calls
    #: ``compdef``, so that first invocation registers the real function and
    #: returns NO candidates; only from the second attempt does ``_comps[grove]``
    #: point at something that completes. Observed exactly that: a fresh shell
    #: has ``_comps[grove]=_grove``, and after one completion it is
    #: ``_grove_completion``. To a user that reads as "completion doesn't work",
    #: because nobody presses TAB twice on a fresh shell to see if it starts.
    #:
    #: Click's own zsh template carries this branch; Typer's dropped it — which
    #: also means Typer's own ``--install-completion`` (it writes ``~/.zfunc``,
    #: an fpath dir) has the same dead first TAB. The guard is ADDITIVE rather
    #: than surgery on Typer's text: the sourced path still hits ``compdef``, and
    #: the autoloaded path now also runs the completion it was called for.
    _ZSH_AUTOLOAD_GUARD: ClassVar[str] = """

# Added by Grove: this file is installed on $fpath and therefore AUTOLOADED,
# while the template above is written to be sourced. When zsh loads it as a
# completion function the body IS the completion, so it has to do the work --
# without this, the first TAB in every new shell only registers and returns
# nothing. Sourcing still takes the `compdef` path above; nothing changes there.
if [[ ${{zsh_eval_context[-1]}} == loadautofunc ]]; then
  _{prog_name}_completion "$@"
fi
"""

    @classmethod
    def render(cls, shell: str, *, prog_name: str = "grove") -> str:
        """The completion script for ``shell``, exactly as ``--show-completion``
        prints it.

        **Comes from Typer's own template, deliberately, even though that means
        importing a private module.** The script and the runtime handler that
        answers it are two halves of ONE protocol, and Typer owns both — so the
        only safe source for one is whoever owns the other.

        The more public-looking route is wrong, and silently so. Building the
        script from ``click.shell_completion.get_completion_class(shell)`` yields
        Click's native zsh script, which asks back with
        ``_GROVE_COMPLETE=zsh_complete``. Typer's runtime handler
        (``typer.completion.shell_complete``) parses that string as
        ``<instruction>_<shell>`` — the reverse of Click 8's own order, kept for
        backwards compatibility — so it reads shell ``complete``, finds no such
        shell, and every TAB prints **"Shell complete not supported."** Typer
        ≤0.25 hid this by registering its own class over Click's; 0.27 stopped,
        so the mismatch only appears on newer builds and only when a real shell
        calls back. Nothing in a unit test of the script's TEXT can see it, which
        is why ``tests/cli/test_completions.py`` instead pins that the
        instruction the script sends is one the handler accepts.
        """
        from typer._completion_shared import get_completion_script  # noqa: PLC0415

        try:
            script = get_completion_script(
                prog_name=prog_name,
                complete_var=cls.complete_var(prog_name),
                shell=shell,
            )
        except Exception as exc:  # Typer exits rather than raising a typed error
            raise GroveError(
                f"cannot generate a {shell!r} completion script — run "
                f"`grove --show-completion` to see what this build supports"
            ) from exc
        return (
            script + cls._ZSH_AUTOLOAD_GUARD.format(prog_name=prog_name)
            if shell == "zsh"
            else script
        )

    @staticmethod
    def complete_var(prog_name: str = "grove") -> str:
        """The env var the generated script uses to call back — Click's rule."""
        return f"_{prog_name.replace('-', '_').upper()}_COMPLETE"

    @staticmethod
    def zsh_dirs() -> list[Path]:
        """Directories on the live zsh ``$fpath``, in the shell's own order.

        Asks ``zsh -i`` so the user's ``.zshrc`` has run and any directory it
        adds is visible. ``FPATH`` in this process is honoured first when it
        happens to be exported, which is what makes the probe testable without
        a shell.
        """
        raw = os.environ.get("FPATH", "")
        if not raw:
            try:
                done = subprocess.run(  # fixed argv, shell=False
                    ["zsh", "-i", "-c", "print -r -- ${(j.:.)fpath}"],
                    capture_output=True,
                    text=True,
                    timeout=_FPATH_PROBE_TIMEOUT,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return []
            raw = done.stdout.strip() if done.returncode == 0 else ""
        return [Path(part).expanduser() for part in raw.split(":") if part.strip()]

    @classmethod
    def zsh_target(cls) -> Path:
        """The directory a zsh install should land in.

        Picks the first directory under the user's home that zsh searches,
        following the shell's OWN precedence order rather than a ranked list of
        names — if two candidates are on ``$fpath``, the one zsh consults first
        is the one to install into, and that tiebreak needs no opinion here.

        Directories outside ``$HOME`` are skipped even when writable: those
        belong to a package manager or to root, and a file Grove drops there
        outlives any uninstall it can offer.

        This is a best GUESS and nothing more, because ``$fpath`` at the end of
        the rc is not the ``$fpath`` ``compinit`` saw. :meth:`zsh_resolves`
        is what actually decides whether the placement worked.
        """
        home = Path.home()
        for directory in cls.zsh_dirs():
            if directory.is_dir() and home in directory.parents and os.access(directory, os.W_OK):
                return directory
        return home / cls.ZSH_FALLBACK_DIR

    @classmethod
    def target(cls, shell: str) -> Path:
        """Where the completion file for ``shell`` belongs."""
        if shell == "zsh":
            return cls.zsh_target() / cls.ZSH_FILENAME
        conventional = cls.CONVENTIONAL_DIRS.get(shell)
        if conventional is None:
            raise GroveError(
                f"Grove cannot place a completion file for {shell!r} automatically — "
                f"run `grove completions show --shell {shell}` and install it yourself."
            )
        conventional_dir, filename = conventional
        return Path.home() / conventional_dir / filename

    @classmethod
    def install(cls, shell: str, *, directory: Path | None = None) -> Path:
        """Write the completion file and return where it landed.

        Creates the directory when missing — safe for the conventional per-shell
        paths and the zsh fallback, both of which are user-owned — and writes
        nothing else. Whether the shell can actually SEE it is a separate
        question only :meth:`zsh_resolves` can answer.
        """
        path = directory.expanduser() / cls._filename(shell) if directory else cls.target(shell)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cls.render(shell) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def zsh_completes() -> bool | None:
        """Does a real zsh, after its rc has run, complete ``grove``?

        ``None`` means zsh could not be asked at all.

        **Deliberately answers only this, and not "from which file".** A first
        version returned the path via ``functions_source``, which looks strictly
        better and is not: that resolves against the fpath as it stands AFTER the
        rc, so it happily names a file in a directory ``compinit`` never scanned.
        Measured here — with the completion cached from an earlier good install,
        a fresh copy written into an after-``compinit`` directory was reported as
        the resolved source. **Every refinement of this probe ran into the same
        wall**, because the one thing needed (the fpath at ``compinit`` time) is
        not recoverable once the rc has finished. So the claim is narrowed to
        what the mechanism supports, and the caller's wording matches: *zsh
        completes grove*, never *this file is the one it uses*.

        **This exists because being on ``$fpath`` does not mean ``compinit`` saw
        it, and nothing about the directory reveals the difference.** ``compinit``
        builds its map once, partway through the rc; a directory appended after
        that line is on the final ``$fpath`` — so it looks perfect to any check
        Grove can make against the path — and is invisible to completion forever.
        That is not a corner case: it is what an ``fpath+=`` line sitting below
        oh-my-zsh does, and the reference host had exactly that, which is how the
        bug was found. Ordering inside somebody's rc is not predictable, so the
        only honest answer comes from running the shell and asking it.

        **It must NOT run ``compinit`` itself, and the first version did.** A
        ``-c`` command runs after the whole rc, so re-running ``compinit`` there
        re-scans the FINAL ``$fpath`` — including the very directories added too
        late for the real one — and cheerfully confirms a placement that is dead
        in an actual shell. Measured: with the file only in an after-``compinit``
        directory, a re-scanning probe reported success while a plain read of
        ``_comps`` correctly reported nothing. A detector more permissive than
        the thing it checks is worse than no detector.

        Reading the live ``_comps`` instead inherits the shell's real answer,
        cache and all. The cost is that a stale dump (oh-my-zsh rebuilds roughly
        daily) reports "not found" for an install that is fine, which is why the
        caller's failure message names the cache as the first suspect rather than
        asserting the directory is wrong.
        """
        probe = "print -r -- ${+_comps[grove]}"
        try:
            done = subprocess.run(  # fixed argv, shell=False
                ["zsh", "-i", "-c", probe],
                capture_output=True,
                text=True,
                timeout=_FPATH_PROBE_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        # An rc is free to print whatever it likes, so read the LAST line only.
        tail = [line for line in done.stdout.strip().splitlines() if line.strip()]
        return tail[-1].strip() == "1" if tail else None

    @classmethod
    def _filename(cls, shell: str) -> str:
        if shell == "zsh":
            return cls.ZSH_FILENAME
        conventional = cls.CONVENTIONAL_DIRS.get(shell)
        return conventional[1] if conventional else f"grove.{shell}"


# ─── `grove completions` ────────────────────────────────────────────────────

completions_app = typer.Typer(
    name="completions",
    help="Generate and install shell tab-completion for the grove command.",
    no_args_is_help=True,
)

# `clean_exit` is imported inside each command body, never at module scope: the
# wiring runs the other way — `cli_workspace` imports `Complete` from here to
# attach it to its parameters — and a module-level import back would close the
# cycle. Dependencies flow inward, and this module is the inner one.


def _current_shell() -> str:
    """The shell that launched this process, defaulting to zsh.

    Typer bundles ``shellingham`` for exactly this, so detection is not
    re-implemented. The fallback is zsh rather than a refusal because the
    detection fails precisely when a shell is not the parent — a script, a CI
    step, an editor terminal — and in those cases a named default beats an error
    the caller cannot act on.
    """
    try:
        import shellingham  # type: ignore[import-untyped]  # noqa: PLC0415

        return str(shellingham.detect_shell()[0])
    except Exception:  # detection is a convenience, never a gate
        return "zsh"


_SHELL_OPTION = typer.Option(
    None,
    "--shell",
    "-s",
    help="zsh, bash or fish. Omit to detect the shell that launched this command.",
)
_DIR_OPTION = typer.Option(
    None,
    "--dir",
    "-d",
    help="Install into this directory instead of the one the shell already searches.",
)


@completions_app.command("show")
def completions_show(shell: str | None = _SHELL_OPTION) -> None:
    """Print the completion script without installing it.

    The escape hatch for a setup Grove should not guess at — a system package,
    a dotfiles repo, a shell whose search path is managed elsewhere. Pipe it
    wherever that setup wants it.
    """
    from grove.tui.cli_workspace import clean_exit  # noqa: PLC0415 - see above

    with clean_exit():
        typer.echo(CompletionScript.render(shell or _current_shell()))


@completions_app.command("install")
def completions_install(
    shell: str | None = _SHELL_OPTION,
    directory: Path | None = _DIR_OPTION,
) -> None:
    """Write the completion script where this shell already looks for it.

    Never edits ``~/.zshrc`` or any other rc file. For zsh the placement is then
    CHECKED by running the shell, because a directory can be on the final
    ``$fpath`` and still be invisible to ``compinit`` — the difference is where
    the rc adds it, which nothing about the path reveals. A dead install is the
    failure worth being loud about, since it looks exactly like a working one.
    """
    from grove.tui.cli_workspace import clean_exit  # noqa: PLC0415 - see above

    resolved = shell or _current_shell()
    with clean_exit():
        path = CompletionScript.install(resolved, directory=directory)

    typer.secho(f"wrote {path}", fg=typer.colors.GREEN)
    if resolved != "zsh":
        typer.echo("Start a new shell to pick it up.")
        return

    completes = CompletionScript.zsh_completes()
    if completes is None:
        typer.echo("Start a new shell to pick it up (could not run zsh to verify).")
        return
    if completes:
        # Says what the probe can support and no more — "zsh completes grove",
        # not "from this file", which it cannot know. compinit also caches its
        # map in a dump oh-my-zsh rebuilds roughly daily, so the CURRENT shell
        # can still lag a good install until that turns over.
        typer.echo(
            "Verified: zsh completes `grove`. Start a new shell to pick this up — "
            "if it doesn't fire, clear zsh's cache: rm -f ~/.zcompdump*"
        )
        return

    # Two causes are indistinguishable from here, so name both rather than
    # assert one — see `zsh_completes` on why the cache is the likelier of them
    # immediately after an install.
    typer.secho(
        "\nzsh does not complete `grove` yet. Two things cause that:", fg=typer.colors.YELLOW
    )
    typer.echo(
        "\n  1. zsh's completion cache predates this file — clear it and start a new shell:\n"
        "         rm -f ~/.zcompdump*\n"
        f"\n  2. {path.parent} is not searched by `compinit`. A directory added AFTER\n"
        "     compinit runs is still on $fpath, so it looks fine and is invisible.\n"
        "     Add this to ~/.zshrc BEFORE `compinit` (before sourcing oh-my-zsh):\n"
        f"         fpath=({path.parent} $fpath)\n"
        "\n     Already keep completions somewhere earlier? Install there instead:\n"
        "         grove completions install --dir ~/.zsh/completions\n"
    )


def register(app: typer.Typer) -> None:
    app.add_typer(completions_app, name="completions")


__all__ = ["Complete", "CompletionScript", "completions_app", "register"]
