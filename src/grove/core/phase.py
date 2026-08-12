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

from collections.abc import Sequence
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

Deliberately ABSENT: ``error``, which already exists on ``AgentActivityState``.
``blocked`` is absent *as a member* for a different and sharper reason — see
:attr:`PhaseClaim.blocked`, which carries it as an orthogonal flag so that
"stuck, and here is how far it got" stays expressible.
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


class PhaseClaim(BaseModel):
    """One claim about progress: how far, a note, and whether it is stuck.

    The atom shared by the workspace's own claim and every per-ticket claim
    beside it, because "how far along is this" is one question whatever it is
    asked about. Splitting it into two near-identical models is how the two
    drift on the next field.

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

    blocked: bool = False
    """The agent cannot finish this, and is saying so rather than going quiet.

    **A FLAG BESIDE THE PHASE, NOT A SEVENTH PHASE**, and the distinction is the
    whole point. "Stuck" and "how far it got before it stuck" are two facts, and
    an orchestrator triaging a fleet needs both: ``scoping + blocked`` is a
    ticket nobody can even start, while ``verifying + blocked`` is work that is
    substantially done and wants one decision. Spending a member of
    :data:`PHASE_ORDER` on it would collapse those into one word and, worse,
    break every client that renders "4 of 6" — a blocked task has no position on
    a linear ramp, so :attr:`index` would have to start lying or start being
    nullable at every call site.

    This is the same reasoning that keeps ``AgentActivityState`` a separate axis
    from ``WorkspaceStatus``, applied one level down. It does NOT duplicate that
    enum's own ``BLOCKED``: there, blocked means *waiting on a human right now*
    and clears the moment they answer; here it means *this task is not
    completable by me*, which survives the agent going idle, dying, or being
    respawned, because it is a claim about the work rather than about the
    process. The reason belongs in :attr:`note`.
    """

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

    @property
    def index(self) -> int:
        """Zero-based position in :data:`PHASE_ORDER` — for progress rendering.

        Stays honest under :attr:`blocked` precisely because blocked is not a
        phase: a stuck task still has a position, which is the number a reader
        wants most.
        """
        return PHASE_ORDER.index(self.phase)

    @property
    def is_terminal(self) -> bool:
        """Whether this claim has converged and wants nothing further.

        ``blocked`` is terminal too — the agent is done with it either way, and
        a fleet view that hid a blocked ticket among the live ones would bury
        the single row most needing a human. What separates them is *who* acts
        next, which the flag itself says.
        """
        return self.phase == "done" or self.blocked


class TicketClaim(PhaseClaim):
    """A :class:`PhaseClaim` about one attached ticket, carrying its key.

    The key is ``f"{provider}:{id}"`` — the identity ``WorkspaceState.ticket_refs``
    already deduplicates on and the exact string ``attach_ticket`` already emits
    on its event. Reusing it means the join needs no new vocabulary, no lookup
    table and no normalizer.

    Grove never composes this key on the agent's behalf at *write* time; it
    SEEDS the file with an entry per attached ticket (:meth:`PhaseFile.seed`) so
    the agent edits keys that are already there rather than deriving a format
    from prose it may have skimmed.
    """

    ticket: str = Field(min_length=1)


class PhaseDocument(PhaseClaim):
    """Exactly what the agent writes into its phase file.

    The workspace's own claim, plus an optional claim per attached ticket. One
    workspace routinely carries several tickets — a cluster of issues, or an
    issue and the PR closing it — and one shared phase cannot say that issue A
    is delivering while issue B is blocked. It answered the question "how is the
    workspace" when the question a tracker asks is "how is *this ticket*".

    ``tickets`` is a MAP here and a sorted tuple on :class:`PhaseReport`, which
    is a deliberate asymmetry rather than an oversight. A map is what a language
    model writes correctly by hand, and this document has exactly one author. A
    report is consumed by the activity tick, which puts it inside a fingerprint
    tuple — so it has to be hashable, which a ``dict`` field is not.
    """

    tickets: dict[str, PhaseClaim] = Field(default_factory=dict)

    @field_validator("tickets", mode="before")
    @classmethod
    def _keep_what_parses(cls, v: object) -> object:
        """Drop the ticket entries that do not validate, keep the ones that do.

        Tolerant-inward applied at the right GRANULARITY. Without this the whole
        model is strict about a nested value: one ticket entry naming a phase
        this Grove does not know fails the document, so the agent loses its
        workspace phase *and* every other ticket's — a total blackout caused by
        one typo in one nested key. Per-entry filtering costs the bad entry
        only, which is exactly what :meth:`PhaseFile.read` already promises for
        the document as a whole.
        """
        if not isinstance(v, dict):
            return {}
        kept: dict[str, PhaseClaim] = {}
        for key, raw in v.items():
            if not isinstance(key, str) or not key:
                continue
            try:
                kept[key] = PhaseClaim.model_validate(raw)
            except ValidationError as exc:
                logger.debug(f"dropping unparseable phase entry for ticket {key!r}: {exc}")
        return kept


class PhaseReport(PhaseClaim):
    """A phase claim plus when it was made — what every consumer receives.

    Inherits the claim rather than restating its fields: a report *is* the
    agent's claim, observed at a time. ``updated_at`` is the file's mtime rather
    than a field the agent writes, because a timestamp is one more thing a model
    can get wrong or omit while the filesystem already records it exactly — and
    staleness is the whole reason a consumer asks for it.

    **One mtime covers the document, ticket claims included.** The file is
    rewritten whole, so there is no per-ticket write time to read; deriving one
    would mean Grove diffing successive reads and remembering the result, which
    is per-process state that a daemon restart silently resets. Consistent with
    this module's standing refusal to render a staleness verdict at all.
    """

    updated_at: datetime

    tickets: tuple[TicketClaim, ...] = ()
    """Per-ticket claims, ordered by key so the tuple is stable.

    A TUPLE, not the document's map, for one hard reason: ``WorkspaceActivity``
    puts this whole report inside its change fingerprint, and a model carrying a
    ``dict`` field is unhashable — the tick would raise on the first workspace
    that reported a ticket. Sorting makes the value deterministic, so an
    unchanged file cannot re-emit a delta just because a mapping iterated in a
    different order.
    """

    def for_ticket(self, key: str) -> TicketClaim | None:
        """This report's claim about one ticket, or ``None`` if it made none.

        ``None`` means *not reported*, never *at step zero* — the same
        distinction the whole axis rests on, applied per ticket. A linear scan
        because a workspace holds a handful of tickets, not thousands; an index
        here would be a dict, which is what the tuple exists to avoid.
        """
        return next((t for t in self.tickets if t.ticket == key), None)


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
        doc = cls._document(worktree, key)
        if doc is None:
            return None
        try:
            mtime = cls.path_for(worktree, key).stat().st_mtime
        except OSError as exc:
            logger.debug(f"phase file vanished between read and stat at {worktree}: {exc}")
            return None
        return cls._report(doc, datetime.fromtimestamp(mtime, tz=UTC))

    @classmethod
    def _document(cls, worktree: Path | str, key: str | None) -> PhaseDocument | None:
        """Parse *key*'s document, or ``None`` for any failure. Never raises."""
        path = cls.path_for(worktree, key)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.debug(f"phase file unreadable at {path}: {exc}")
            return None
        try:
            return PhaseDocument.model_validate_json(raw)
        except ValidationError as exc:
            logger.debug(f"phase file at {path} is not a valid phase document: {exc}")
            return None

    @staticmethod
    def _report(doc: PhaseDocument, updated_at: datetime) -> PhaseReport:
        """Project a document onto the report shape consumers receive.

        The one place the document's ticket MAP becomes the report's sorted
        TUPLE, so nothing else has to know the two differ (see
        :class:`PhaseDocument` for why they do).
        """
        return PhaseReport(
            phase=doc.phase,
            note=doc.note,
            blocked=doc.blocked,
            updated_at=updated_at,
            tickets=tuple(
                TicketClaim(ticket=key, phase=c.phase, note=c.note, blocked=c.blocked)
                for key, c in sorted(doc.tickets.items())
            ),
        )

    @classmethod
    def write(
        cls,
        worktree: Path | str,
        key: str,
        phase: TaskPhase,
        note: str | None = None,
        *,
        blocked: bool = False,
        ticket: str | None = None,
    ) -> PhaseReport:
        """Record a phase claim — the seam behind the CLI verb and the MCP tool.

        The in-workspace agent does NOT come through here (it has no Grove code
        to call); this exists so an orchestrator, or a human, can set or correct
        a claim from outside. Loud on failure, unlike :meth:`read` — a caller
        that asked to write is entitled to know it did not happen.

        Takes a *key*, never the legacy path: a write always lands on the
        per-agent file, so setting a phase from outside is the same claim, in the
        same place, as the one the agent itself would have written.

        With *ticket* set the claim lands on that ticket's entry and the
        workspace's own claim is left alone; without it, the reverse. **Either
        way this is a read-modify-write of the whole document**, because the
        file is rewritten whole and a partial write would drop whatever it did
        not mention. The agent may be writing concurrently and last-writer-wins,
        which is tolerable *here* precisely because the ticket LIST is never
        sourced from this file — the store owns that, so the worst a lost update
        can cost is one stale claim that the agent's next transition corrects.
        """
        path = cls.path_for(worktree, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        held = cls._document(worktree, key)
        claim = PhaseClaim(phase=phase, note=note, blocked=blocked)
        if ticket is None:
            doc = PhaseDocument(
                phase=claim.phase,
                note=claim.note,
                blocked=claim.blocked,
                tickets=dict(held.tickets) if held else {},
            )
        else:
            doc = PhaseDocument(
                phase=held.phase if held else phase,
                note=held.note if held else None,
                blocked=held.blocked if held else False,
                tickets={**(held.tickets if held else {}), ticket: claim},
            )
        return cls._replace(path, doc)

    @classmethod
    def seed(cls, worktree: Path | str, key: str, tickets: Sequence[str]) -> None:
        """Ensure the file carries an entry for every attached ticket.

        **This is what lets the agent edit keys instead of composing them.** The
        alternative — telling it the format in prose and hoping — puts a string
        it has never seen between an honest report and a dropped one, and the
        drop is silent. Seeding costs one write at launch and one per attach.

        Deliberately additive: an existing entry is never touched (it is the
        agent's own claim), and a DETACHED ticket's entry is left behind rather
        than pruned, because the join reads the ticket list from the store — a
        stale entry there is inert, while a write racing the agent is not.

        Best-effort by contract, like :meth:`ensure_dir`: a read-only worktree
        costs a convenience, never a launch.
        """
        missing = [t for t in tickets if t]
        if not missing:
            return
        try:
            held = cls._document(worktree, key)
            entries = dict(held.tickets) if held else {}
            fresh = {t: PhaseClaim(phase=PHASE_ORDER[0]) for t in missing if t not in entries}
            if held is not None and not fresh:
                return
            path = cls.path_for(worktree, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            cls._replace(
                path,
                PhaseDocument(
                    phase=held.phase if held else PHASE_ORDER[0],
                    note=held.note if held else None,
                    blocked=held.blocked if held else False,
                    tickets={**entries, **fresh},
                ),
            )
        except (OSError, ValidationError) as exc:
            logger.debug(f"phase file not seedable under {worktree}: {exc}")

    @classmethod
    def _replace(cls, path: Path, doc: PhaseDocument) -> PhaseReport:
        """Publish *doc* at *path* atomically and report what now stands there.

        Atomic replace, matching ``JsonWorkspaceStore``: a reader on the poll
        path must never observe a half-written file.
        """
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(doc.model_dump_json(exclude_none=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return cls._report(doc, datetime.fromtimestamp(path.stat().st_mtime, tz=UTC))
