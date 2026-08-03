"""The task-phase axis — what an agent says it is DOING about its task.

Grove already carries two status axes, and this is deliberately a **third**,
orthogonal to both: :class:`~grove.core.workspace.WorkspaceStatus` describes the
workspace/tmux lifecycle (is there a session, is the container up) and
:class:`~grove.core.agents.AgentActivityState` describes the agent's moment-to-
moment activity (is it typing, waiting, blocked). Neither answers *how far
through the task* the agent believes it is — an agent is equally ``working``
while it is still reading the ticket and while it is opening the PR, and an
orchestrator watching twenty workspaces needs to tell those apart. Hence
``WORKING + planning`` and ``WORKING + verifying`` are both valid and mean
different things; folding the phase into either existing enum would destroy the
distinction that motivates it.

**The phase is REPORTED, not derived.** Nothing in a transcript reliably says
"I have finished planning" — that is a claim only the agent can make. So this
module owns one narrow contract: the agent writes a small JSON file into its own
worktree and Grove reads it. Everything else about the mechanism follows from
one constraint discovered by measurement rather than assumption:

**A containerized agent can reach Grove by exactly two channels, and neither is
a request.** The daemon binds loopback and the container gets no host-gateway
route (Grove's own ``host.docker.internal`` allow rule emits zero rules on
Linux), so HTTP is out; nothing mounts the ``grove`` package into the container,
so the CLI is out. What DOES cross is the worktree itself — it is bind-mounted,
so a file the agent writes inside the container is on the host filesystem
immediately. A file is therefore the only channel that works for host and
container, Claude and Codex, and any harness Grove has not met yet, since
writing a file is the one capability every coding agent has.

**A directory does NOT identify an agent.** Writing to a path derived from the
agent's own cwd is attractive because the agent never needs to know its own
identity — and wrong for two configurations Grove already ships. A ROOT-placed
workspace's worktree IS the repo root, so every root workspace on a repo would
resolve to one path and overwrite each other silently; and ``grove agent add``
runs several agents in one container over one worktree, so N agents would
resolve to one path and whichever wrote last would speak for all of them. Cwd
identifies a *directory*, never an agent.

So Grove composes an absolute, per-agent path and **publishes it in the launch
env** as :data:`PhaseFile.PATH_ENV`; the agent writes exactly there and
interprets nothing. Three properties fall out, and each was a bug before:

* Keyed by workspace id **and** agent slot, so co-tenants of one directory never
  collide. Grove knows both facts at launch; the agent needs neither.
* Still **under the worktree**, so a containerized agent's write is bind-mount
  visible on the host with no new mount — the path is merely re-rooted at the
  container's own workspace folder on the way into the env.
* Nothing left to resolve against a cwd, which closes the nested-``project_subpath``
  ambiguity: "write ``.grove/phase.json`` in your worktree" is exactly the
  sentence an agent sitting in ``<worktree>/sub`` resolves to the wrong file.

The pre-per-agent single file is still READ (never written) so a workspace that
had already reported keeps working, and ``grove phase`` from a plain worktree
behaves as it did.

The files are git-excluded (see ``GitRepo.ensure_excluded``) for a correctness
reason, not a cosmetic one: ``git worktree remove`` refuses to run while
untracked files are present, so an un-excluded artifact here would break
``pause`` and ``kill`` on every workspace that ever reported a phase. The
exclude is what pins the LOCATION: a pattern containing a slash in
``info/exclude`` is anchored to the working-tree root, so ``.grove/phase/``
covers ``<worktree>/.grove/phase/`` and nothing nested under it (verified
against real git in both directions).

**Two things this module deliberately does NOT do**, both re-proposable:

* **No staleness verdict.** An agent that dies mid-task leaves its last phase
  standing, and that is correct: a phase is a claim about the TASK, and a task
  in ``implementing`` for three hours is a long task, not a stale report. Whether
  the agent is still alive is what the other two axes already answer, better —
  ``WorkspaceStatus`` (is the session/container up) and ``AgentActivityState``
  (is it working, waiting, dead — it records a launch that exited). Folding age
  into the phase would recreate exactly the conflation this axis exists to avoid,
  with a threshold no consumer's policy would fit. :attr:`PhaseReport.updated_at`
  is on the wire already, so a consumer that wants an age has one; Grove supplies
  the mechanism and leaves the policy to whoever is watching.
* **No survival past ``pause``.** ``pause`` removes the worktree and the phase
  goes with it. Transcripts outlive worktrees because they live in the agent
  tool's own directory, which Grove never chose; the phase file is in the
  worktree *on purpose* (that is the bind-mount property above), so retaining it
  would mean copying it into a second persisted store on the way out. It would
  also be a claim by nobody — no agent is running to stand behind it — and
  ``resume`` relaunches an agent that reports afresh. The durable record of what
  a workspace did is its commit history, which is already the rule the activity
  card follows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, get_args

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

TaskPhase = Literal[
    "scoping",
    "planning",
    "implementing",
    "verifying",
    "delivering",
    "done",
]
"""How far through its task the agent says it is.

A closed set rather than free text because every client renders it as a badge
and an orchestrator compares it across workspaces — the same reasoning that
makes ``WorkspaceStatus`` an enum. Six members, linear and converging.

Deliberately ABSENT: ``blocked`` and ``error``. Both already exist on
``AgentActivityState``, and duplicating them here would recreate exactly the
conflation this axis is separate to avoid — an agent blocked on a question is
still *in* some phase, and the orchestrator wants both facts.
"""

PHASE_ORDER: Final[tuple[TaskPhase, ...]] = get_args(TaskPhase)
"""The phases in order, derived from the type so the two cannot drift.

Order is meaningful — clients render progress through it — but Grove never
enforces monotonicity: an agent that discovers in ``verifying`` that its design
was wrong is *right* to report ``planning`` again, and a tool that refused the
transition would be punishing the honest report it exists to collect.
"""


NOTE_CAP: Final = 200
"""The note renders as a one-line badge subtitle; anything longer is a paragraph
the agent should have put in its todo list instead."""


class PhaseDocument(BaseModel):
    """Exactly what the agent writes into ``.grove/phase.json``.

    Pydantic rather than a hand-parsed dict because this genuinely *is* a
    contract between two programs — an agent constructs it, Grove consumes it —
    so it meets the repo's own test for a wire shape even though it travels by
    file rather than by HTTP. Making it a model moves every validation rule to
    the point of definition: the vocabulary is enforced by the annotation, the
    note is normalized by a validator, and :meth:`PhaseFile.read` is left with
    no branching of its own to get wrong.

    ``extra="ignore"``, deliberately against the house ``extra="forbid"``
    default. Forbidding is right for a request body Grove's own clients build,
    where an unknown key means a caller bug worth surfacing loudly. Here the
    author is a language model writing JSON by hand: a stray key should cost
    nothing, because the alternative is discarding a perfectly good phase over a
    field nobody reads. Tolerant inward, strict outward.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    phase: TaskPhase
    note: Annotated[str | None, Field(max_length=NOTE_CAP)] = None

    @field_validator("note", mode="before")
    @classmethod
    def _flatten(cls, v: object) -> str | None:
        """Collapse whitespace and truncate, or drop the note entirely.

        ``mode="before"`` so a multi-line note is normalized *into* the length
        bound rather than rejected by it — a wrapped note is a formatting
        detail, and failing the whole document over it would lose the phase too.
        A non-string is dropped rather than coerced: ``str(["a"])`` would render
        list syntax back to the user as if the agent had written it.
        """
        if not isinstance(v, str):
            return None
        flat = " ".join(v.split())
        return flat[:NOTE_CAP] or None


class PhaseReport(PhaseDocument):
    """A phase claim plus when it was made — what every consumer receives.

    Inherits the document rather than restating its fields: a report *is* the
    agent's claim, observed at a time. ``updated_at`` is the file's mtime rather
    than a field the agent writes, because a timestamp is one more thing a model
    can get wrong or omit while the filesystem already records it exactly — and
    staleness is the whole reason a consumer asks for it.
    """

    updated_at: datetime

    @property
    def index(self) -> int:
        """Zero-based position in :data:`PHASE_ORDER` — for progress rendering."""
        return PHASE_ORDER.index(self.phase)

    @property
    def is_terminal(self) -> bool:
        """Whether the agent considers the task converged."""
        return self.phase == "done"


class PhaseFile:
    """The agent-written phase file: where it lives, how it is read and written.

    All-classmethod because the state it owns is on disk, not on an instance —
    there is nothing per-``PhaseFile`` to hold. Reads are best-effort by
    contract (they feed render paths, exactly like ``peek``): a missing,
    unreadable, malformed, or unknown-phase file yields ``None``, never an
    exception. An agent typo must cost its own badge, never a caller's snapshot.
    """

    RELDIR: Final = ".grove/phase"
    """Worktree-relative directory holding one file per agent.

    Under ``.grove/`` rather than in a dot-directory of its own — one Grove
    directory a user already knows beats a second one they have to learn — and a
    DIRECTORY rather than a file because a worktree hosts one agent only in the
    simple case (see the module docstring)."""

    LEGACY_RELPATH: Final = ".grove/phase.json"
    """The pre-per-agent single file. **Read-only back-compat**: a workspace
    that reported before the per-agent layout landed keeps rendering its last
    phase, and nothing writes here again."""

    EXCLUDES: Final = (RELDIR + "/", LEGACY_RELPATH)
    """What ``GitRepo.ensure_excluded`` must cover — both shapes, because a
    workspace created before the per-agent layout landed can still be carrying
    the legacy file."""

    PATH_ENV: Final = "GROVE_PHASE_FILE"
    """The launch-env variable naming this agent's own file, absolutely, in the
    agent's OWN namespace (a container's path inside the container). The agent
    writes exactly there and interprets nothing — which is the whole fix."""

    @staticmethod
    def key_for(workspace_id: str, *, agent: str | None = None) -> str:
        """The file key for one agent: the workspace, plus its slot if it has one.

        Both halves are load-bearing and neither substitutes for the other. The
        workspace id separates co-tenants of one DIRECTORY (every ROOT workspace
        on a repo shares the repo root); the agent slot separates co-tenants of
        one WORKSPACE (``grove agent add``'s extra in-container agents).

        The workspace's own agent is keyed by the bare id rather than by the
        primary slot name, deliberately: that name is ``container.tmux.session``,
        an operator-settable config value, and keying on it would orphan a
        workspace's phase file the day somebody renamed it.

        ``.`` is the separator because a slot name cannot contain one — tmux
        gives it meaning inside a target spec, so ``ContainerAgent.validate_name``
        already refuses it — which makes the composite unambiguous by
        construction rather than by escaping.
        """
        return workspace_id if agent is None else f"{workspace_id}.{agent}"

    @classmethod
    def relpath(cls, key: str) -> str:
        """*key*'s worktree-relative path, POSIX — the form a container re-roots."""
        return f"{cls.RELDIR}/{key}.json"

    @classmethod
    def path_for(cls, worktree: Path | str, key: str | None) -> Path:
        """The absolute path of *key*'s phase file under *worktree*.

        ``key=None`` names the legacy single file — the read-only back-compat
        path, never a write target.
        """
        rel = cls.LEGACY_RELPATH if key is None else cls.relpath(key)
        return Path(worktree) / rel

    @classmethod
    def ensure_dir(cls, worktree: Path | str) -> None:
        """Create the phase directory, best-effort, before an agent needs it.

        Called at every launch so the agent's write is a plain file write with
        no ``mkdir`` step to explain — the instructions string is spent on every
        MCP connection, so a sentence saved there is worth two lines here. An
        empty directory is invisible to git, and it is excluded anyway, so this
        cannot affect ``pause``/``kill``. Best-effort by contract: a read-only
        worktree costs a phase badge, never a launch.
        """
        try:
            (Path(worktree) / cls.RELDIR).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.debug(f"phase dir not creatable under {worktree}: {exc}")

    @classmethod
    def read(cls, worktree: Path | str, key: str | None) -> PhaseReport | None:
        """The agent's current phase claim, or ``None`` if it has not made one.

        ``None`` deliberately means "no phase reported" and is NOT a member of
        :data:`PHASE_ORDER` — a consumer must be able to tell "has not reported"
        from "is scoping", because the first is a fleet-health signal about the
        agent and the second is progress on the task.

        Every failure mode collapses to that same ``None``: absent, unreadable,
        not JSON, not an object, or naming a phase this Grove does not know. The
        last is worth calling out — an unrecognized phase is either an agent
        typo or a newer vocabulary, and dropping it is right for both, because
        rendering it would put a value on screen that no palette, glyph, or
        ordering can place.
        """
        path = cls.path_for(worktree, key)
        try:
            raw = path.read_bytes()
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.debug(f"phase file unreadable at {path}: {exc}")
            return None
        try:
            doc = PhaseDocument.model_validate_json(raw)
        except ValidationError as exc:
            logger.debug(f"phase file at {path} is not a valid phase document: {exc}")
            return None
        return PhaseReport(
            phase=doc.phase,
            note=doc.note,
            updated_at=datetime.fromtimestamp(mtime, tz=UTC),
        )

    @classmethod
    def write(
        cls, worktree: Path | str, key: str, phase: TaskPhase, note: str | None = None
    ) -> PhaseReport:
        """Record a phase claim — the seam behind the CLI verb and the MCP tool.

        The in-workspace agent does NOT come through here (it has no Grove code
        to call); this exists so an orchestrator, or a human, can set or correct
        a workspace's phase from outside. Loud on failure, unlike :meth:`read` —
        a caller that asked to write is entitled to know it did not happen.

        Takes a *key*, never the legacy path: a write always lands on the
        per-agent file, so setting a phase from outside is the same claim, in the
        same place, as the one the agent itself would have written.
        """
        path = cls.path_for(worktree, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = PhaseDocument(phase=phase, note=note)
        # Atomic replace, matching JsonWorkspaceStore: a reader on the poll path
        # must never observe a half-written file.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(doc.model_dump_json(exclude_none=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return PhaseReport(
            phase=doc.phase,
            note=doc.note,
            updated_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        )
