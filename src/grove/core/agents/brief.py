"""The first-turn brief — one pointer at the ``working-in-grove`` skill.

Grove ships a skill telling the agent inside a workspace how to report its task
phase, keep its todo list honest, and attach the pull request it opened. Agents
did not load it, because nothing ever told them it applied to them: a skill is
discovered from its own description, and "you are inside a Grove workspace" is
precisely the fact an agent cannot observe.

So this module owns one short text delivered ONCE, on the agent's first turn.

**It points; it never restates.** The six phase names, the file contract, the
publish warning and the PR-attach rule all live in the skill, which the agent
reads on demand and only when it needs them. A brief that repeated any of that
would spend context in every session forever and drift from the skill the day
either one is edited — and the skill is also published as a plugin, so the drift
would be invisible from here.

**Why it lives beside the hook rather than in the engine.** The delivery channel
is the ``UserPromptSubmit`` hook: for that one event Claude Code injects the
hook's stdout into the model's context, so the brief is text this package's own
entry point prints. The engine's part is a single env var naming the file
(:attr:`AgentBrief.PATH_ENV`, published by ``_launch_env`` exactly like
``GROVE_PHASE_FILE``), which is what makes the option per-workspace even though
the hook settings file is host-global and shared by every workspace on the
machine.

**Once per session is the whole difficulty**, because ``UserPromptSubmit`` fires
on every prompt a human sends. The marker lives beside the hook sidecars — Grove
state, on the host — and never in the worktree, where it would surface as
untracked work in the user's own ``git status``. It is claimed with an exclusive
create, so "have I already briefed this session" is answered by the filesystem
rather than by a read-then-write nothing serializes.

**Nothing here asks whether it is running under Grove**, because nothing else
can be: Grove's hooks live only in the file it passes via ``claude --settings``,
never in the user's own ``~/.claude/settings.json``, so a human running
``claude`` by hand in the same worktree runs no hook at all.

A kind with no such hook — codex, and any agent launched into a container, where
the hook command falls back to spooling raw payloads and prints nothing — is
briefed by the engine instead, by prepending :attr:`AgentBrief.TEXT` to the
create-time initial prompt. Same words, same first turn, no extra turn spent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

BRIEF_SKILL: Final = "working-in-grove"
"""The skill the brief exists to point at. Named here so the text and the
packaged skill directory (``src/grove/skills/<name>/``) cannot drift silently."""


class AgentBrief:
    """The brief text, where it is rendered, and the once-per-session read.

    All-classmethod for the same reason as :class:`~grove.core.phase.PhaseFile`:
    the state it owns is on disk, so there is nothing per-instance to hold. Every
    method is best-effort by contract — this runs inside a hook process, where a
    raised exception surfaces in the agent's own UI and a non-zero exit blocks
    the user's prompt outright.
    """

    PATH_ENV: Final = "GROVE_BRIEF_FILE"
    """Launch-env variable naming the rendered brief.

    Presence IS the opt-in: the hook settings file is host-global and shared by
    every workspace, so a per-workspace choice cannot be expressed there, while
    the launch env is composed per workspace and per launch. The hook process
    inherits it from the agent, exactly as ``GROVE_PHASE_FILE`` reaches the
    agent itself."""

    MARKER_DIRNAME: Final = "briefed"
    """Directory of "this session has been briefed" markers, a child of the hook
    sidecar directory so the one argument every hook consumer already passes
    locates it too — and so it inherits that directory's lifecycle, one empty
    file per session beside the one JSON sidecar per session already there."""

    TEXT: Final = f"""\
You are the coding agent in a Grove workspace: a git worktree Grove created for \
one task, on this host or inside a container. What you report — your task phase, \
your todo list, and the pull request you attach — is published onto every ticket \
attached to this workspace, where people who never read your transcript are \
watching.

Load the `{BRIEF_SKILL}` skill and follow it. It carries the whole contract; \
this note only tells you that it applies to you.
"""
    """The brief. Two paragraphs, on purpose.

    It says three things and no more: where the agent is, that what it reports
    is published to an audience, and the name of the skill that says what to do
    about it. Everything a reader might want to add here — the phase vocabulary,
    the file path, the note length, the PR-attach call — is already in the skill,
    one tool call away, and is read there when it is needed rather than paid for
    in every session that never reports anything."""

    @classmethod
    def render(cls, path: Path) -> Path | None:
        """Write the brief to *path*; ``None`` if it could not be written.

        Rewritten on every launch so an edit to :attr:`TEXT` reaches an existing
        installation the next time a workspace starts, and best-effort like every
        other rendered control file: an unwritable config dir costs the brief,
        never the launch. Returning ``None`` is what lets the caller withhold the
        env var, so the variable and the file can never disagree — a variable
        naming a file that does not exist is a hook that reads nothing on every
        prompt of every session.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(cls.TEXT, encoding="utf-8")
        except OSError:
            return None
        return path

    @classmethod
    def marker_dir(cls, sidecar_dir: Path) -> Path:
        """Where the per-session markers live under *sidecar_dir*."""
        return sidecar_dir / cls.MARKER_DIRNAME

    @classmethod
    def consume(cls, session_id: str, *, brief_path: Path, sidecar_dir: Path) -> str | None:
        """The brief for *session_id*'s FIRST prompt, and ``None`` every time after.

        The marker is claimed before the text is returned, and it is claimed with
        an exclusive create — so a second prompt (or a second hook process racing
        the first) gets ``None`` rather than a duplicate brief. The order matters
        in one direction only: claiming first can at worst lose a brief nobody
        has seen yet, while returning first would repeat it on every prompt of
        the session whenever the claim failed.

        ``None`` for every failure — no file, an empty one, an unwritable marker
        directory — because the caller is a hook and the honest degraded
        behaviour is silence.
        """
        if not session_id or "/" in session_id or session_id.startswith("."):
            return None
        try:
            text = brief_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not text:
            return None
        marker = cls.marker_dir(sidecar_dir) / session_id
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch(exist_ok=False)
        except OSError:
            return None
        return text
