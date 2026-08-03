"""Claude Code transcript introspection — the first :class:`AgentAdapter`.

One concern: turn Claude Code's session JSONL into the normalized
:class:`AgentActivity`. Decomposed into three atomic, private classes so each is
nameable in a sentence and testable on its own:

- :class:`_ClaudeHome` — *where* the transcripts live (config-dir resolution +
  the lossy cwd encoding + globbing). The only filesystem side effect.
- :class:`_Record` — *what one line is* (identity, classification, metrics for a
  single JSONL entry). All the "is this a real human turn?" subtlety lives here.
- :class:`_TranscriptParser` — *the aggregate* (one pass over de-duplicated,
  time-sorted records → activity + digest).

``ClaudeCodeAdapter`` is the thin public seam that wires filesystem → parser.

Grounding (verified against real on-host transcripts, Claude Code 2.1.x):
- Transcript path is ``<config>/projects/<encoded-cwd>/<session-uuid>.jsonl``;
  the encoding (every non-alphanumeric char → ``-``, per the Agent SDK sessions
  guide) is **lossy and non-reversible**, so we glob by the known UUID and
  never decode the folder name back to a cwd.
- A ``type:"user"`` line is usually **not** a human turn: ``tool_result`` blocks
  carry ``role:"user"`` too (one real session: 4683 user lines, 80 real turns).
  The real-turn filter is the whole game — see :meth:`_Record.is_human_turn`.
- Assistant ``stop_reason`` is ``"tool_use"`` while working, ``"end_turn"`` when
  the turn completes — a status signal with no LLM required.
- Booleans like ``isSidechain`` arrive as JSON ``true``/``false`` but defensive
  coercion also tolerates the string forms; never trust the wire type.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core.agents.model import (
    TASK_TOOL_NAMES,
    AgentActivity,
    AgentActivityState,
    AgentMessage,
    AgentQuestion,
    AgentSession,
    AnswerSelection,
    ContentBlock,
    ControlScope,
    DigestEntry,
    FileEdit,
    FinalResult,
    MessageRole,
    OrderedDigest,
    SessionControl,
    SessionControls,
    SessionRef,
    SessionSummary,
    SessionTurn,
    TaskBoard,
    TodoList,
    TokenUsage,
    final_result_from_messages,
    latest_todo_from_messages,
)
from grove.core.agents.transcript_cache import ResultMemo, TranscriptCache
from grove.core.tmux import SendKey, SendOp

# Markers that flag a ``type:"user"`` line as machinery, not a human turn:
# slash-command echoes, bash tool I/O, the post-compaction caveat banner, and
# the compaction summary prefix. Matching any one excludes the line.
# Background-task completion notices (sub-agents, background shells) are
# delivered into the conversation as plain ``type:"user"`` lines wrapping this
# XML envelope (verified on-host, Claude Code 2.1.x). They are notifications
# the agent received, never human turns — without the marker they rendered as
# raw ``<task-notification>…`` "user prompts" in every client.
_NOTIFICATION_MARKER = "<task-notification>"

# A relayed message from a peer teammate session (verified CC 2.1.209
# in_process_teammate flavor) — the completion signal for an Agent-
# tool spawn with a ``name`` (a "teammate"), which returns nothing shaped
# like ``_NOTIFICATION_MARKER`` at all. Landed as a plain ``type:"user"``
# STRING-content line wrapping ``<teammate-message teammate_id="...">``
# around an embedded JSON payload (e.g. ``{"type":"idle_notification",...}``).
_TEAMMATE_MESSAGE_MARKER = "<teammate-message"

_NON_HUMAN_MARKERS: tuple[str, ...] = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "Caveat:",
    "This session is being continued from a previous",
    _NOTIFICATION_MARKER,
    _TEAMMATE_MESSAGE_MARKER,
)

# The harness tools that spawn a sub-agent. ``Task`` is the pre-2.1 name of the
# ``Agent`` tool; both appear in transcripts depending on the Claude Code
# version. ``Workflow`` ("ultracode") is a third, distinct spawn shape —
# its launch ack is ``async_launched``-flavored (see ``_ASYNC_ACK_STATUSES``)
# and its workers persist recursively under ``subagents/workflows/wf_<id>/``.
_SUBAGENT_TOOLS = frozenset({"Agent", "Task", "Workflow"})

# toolUseResult.status values marking a tool_result as a LAUNCH ACK, not a
# real return (verified CC 2.1.209): "teammate_spawned" (in-process
# Agent-tool teammate, mailbox-delivered — no run_in_background flag exists
# for this flavor, backgrounding is implicit) and "async_launched" (a
# Workflow ("ultracode") run, whose own async job id rides `toolUseResult
# .taskId` — a DIFFERENT id space than the Task-board taskId TaskCreate/
# TaskUpdate use, but the one `TaskOutput` polls with).
_ASYNC_ACK_STATUSES = frozenset({"teammate_spawned", "async_launched"})

# Sentinel model id Claude Code writes for interrupts / synthetic lines; never a
# real model and never counted toward token usage or the displayed model.
_SYNTHETIC_MODEL = "<synthetic>"

_DIGEST_MAX_ENTRIES = 60
_DIGEST_TEXT_CAP = 200
_TASK_TEXT_CAP = 500


def _as_bool(value: Any) -> bool:
    """Coerce a JSON-ish truthy flag, tolerating the string forms.

    ``isSidechain`` / ``isMeta`` normally arrive as real booleans, but transcript
    records are heterogeneous external data — some tooling has emitted ``"false"``
    as a string, where a naive ``bool("false")`` is ``True``. Narrow at the edge.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _parse_timestamp(value: Any) -> datetime | None:
    """ISO-8601 (``...Z`` accepted) → aware datetime, or ``None`` on anything odd.

    Always aware: a tz-less string is assumed UTC rather than returned naive —
    downstream freshness math subtracts these from ``utcnow`` and a naive
    datetime would raise mid-poll instead of degrading.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class _ClaudeHome:
    """Resolves *where* Claude Code keeps its transcripts.

    Pure path logic + read-only globbing. Reads the environment live on each
    call (not at construction) so a test can redirect ``CLAUDE_CONFIG_DIR`` per
    case and production picks up a relocated config dir without a restart.
    """

    @staticmethod
    def config_dirs() -> list[Path]:
        """Every config-dir BASE in the cascade, de-duplicated (existence NOT
        required — a caller globs and a missing dir just yields nothing).

        Order: ``CLAUDE_CONFIG_DIR`` (comma-separated, like ccusage) → the XDG
        ``~/.config/claude`` → the legacy ``~/.claude``. This is the one place the
        cascade is defined; ``projects_dirs`` (transcripts) and the controls scan
        (commands/skills live directly under a config dir) both project off
        it, so a relocated profile is honoured by both without drift.
        """
        candidates: list[Path] = []
        raw = os.environ.get("CLAUDE_CONFIG_DIR", "")
        for part in raw.split(","):
            cleaned = part.strip()
            if cleaned:
                candidates.append(Path(cleaned).expanduser())
        home = Path.home()
        candidates.append(home / ".config" / "claude")
        candidates.append(home / ".claude")

        seen: set[Path] = set()
        out: list[Path] = []
        for base in candidates:
            key = base.resolve() if base.exists() else base
            if key not in seen:
                seen.add(key)
                out.append(base)
        return out

    @classmethod
    def projects_dirs(cls) -> list[Path]:
        """Every existing ``projects/`` dir across the config-dir cascade
        (:meth:`config_dirs`), de-duplicated, only the ones that exist.
        """
        seen: set[Path] = set()
        out: list[Path] = []
        for base in cls.config_dirs():
            projects = base / "projects"
            key = projects.resolve() if projects.exists() else projects
            if key in seen:
                continue
            seen.add(key)
            if projects.is_dir():
                out.append(projects)
        return out

    @staticmethod
    def encode_cwd(cwd: Path) -> str:
        """Forward-encode a cwd to Claude's folder name.

        The documented rule (Agent SDK sessions guide) is *every*
        non-alphanumeric character → ``-`` — not just ``/`` ``.`` ``_`` — so a
        cwd containing ``@``, ``+``, or a space still hits the fast path. Only
        used to *guess* the most-likely directory; the encoding is lossy, so a
        miss falls back to a UUID glob — we never rely on decoding this back
        into a path.
        """
        return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))

    @classmethod
    def locate(cls, cwd: Path, session_id: str) -> list[Path]:
        """All transcript files for ``session_id``: main thread first, sub-agents after.

        Globs by the unique UUID across every config dir (never by the lossy
        folder name). When the same UUID resolves under multiple project folders
        — a real collision, since the cwd encoding is lossy — the one whose
        first record's ``cwd`` matches ``cwd`` wins; otherwise all are returned
        and the parser's content-level de-dup sorts it out.
        """
        encoded = cls.encode_cwd(cwd)
        mains: list[Path] = []
        subagents: list[Path] = []
        for projects in cls.projects_dirs():
            # Fast path: the cwd we expect, checked directly before any glob.
            fast = projects / encoded / f"{session_id}.jsonl"
            if fast.is_file():
                mains.append(fast)
            for match in projects.glob(f"*/{session_id}.jsonl"):
                if match.is_file() and match not in mains:
                    mains.append(match)
            # Sub-agent transcripts (Claude Code 2.1.2+): <uuid>/subagents/agent-*.jsonl
            # — globbed RECURSIVELY: a Workflow ("ultracode") worker
            # nests one level deeper, at
            # <uuid>/subagents/workflows/wf_<runId>/agent-*.jsonl. ``**``
            # matches zero-or-more intermediate dirs, so this still covers the
            # flat, non-Workflow case too.
            for match in projects.glob(f"*/{session_id}/subagents/**/agent-*.jsonl"):
                if match.is_file():
                    subagents.append(match)

        if len(mains) > 1:
            preferred = [p for p in mains if cls._first_cwd(p) == str(cwd)]
            if preferred:
                mains = preferred
        return [*mains, *subagents]

    @staticmethod
    def read_subagent_meta(path: Path) -> dict[str, Any]:
        """Best-effort read of one sub-agent transcript's sibling ``.meta.json``
        (``{agentType, description, toolUseId}``, verified on-host — Claude Code
        writes it fire-and-forget beside ``agent-{agentId}.jsonl``) — the fleet
        reader's identity + spawn-correlation source. ``toolUseId`` is
        the exact spawning tool_use id on the MAIN thread; this is deliberately
        NOT the same thing as ``sourceToolAssistantUUID`` (surfaced on the spine
        as ``AgentMessage.parent_tool_use_id``) — verified against 1300+ real
        on-host sub-agent transcripts, that field mirrors the
        record's own ``parentUuid`` in every sample (same-thread chaining), never
        the main transcript's spawning call, so it cannot correlate a thread back
        to its spawn. Missing or malformed sidecar → ``{}``, never raised —
        identity degrades to ``None``, the same defensive posture as every other
        parse path here.
        """
        meta_path = path.with_name(f"{path.stem}.meta.json")
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @classmethod
    def discover(cls, cwd: Path, *, exclude_id: str | None) -> list[str]:
        """Session ids of transcripts recorded for ``cwd`` (excluding ``exclude_id``),
        ordered **most-recently-active first** (by transcript mtime).

        The newest-first order is load-bearing for the dashboard: a workspace
        with no Grove-minted id adopts the *live* session by taking the first
        result, so an arbitrary alphabetical order would surface a dead one.
        """
        return [sid for sid, *_ in cls.discover_paths(cwd, exclude_id=exclude_id)]

    @classmethod
    def discover_paths(
        cls, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, Path, float, datetime | None]]:
        """``(session_id, transcript_path, mtime, birth)`` for every session
        recorded in ``cwd``, newest-first by mtime — the one scan behind
        ``discover`` (ids for the dashboard), ``discover_births`` (the cheap
        adoption pre-filter), and ``list_sessions`` (summaries for the
        explorer).

        Scans the forward-encoded candidate folder under each config dir — a
        single directory listing, not a recursive glob — and confirms each by
        the in-line ``cwd`` rather than trusting the lossy folder name.
        Sub-agent files (in a ``<uuid>/subagents/`` subdir) are skipped; only
        top-level ``<uuid>.jsonl`` session files count. ``birth`` rides out of
        the SAME bounded head read that confirms the cwd — no extra I/O — so the
        adoption gate can reject a historical transcript without a full parse.
        """
        encoded = cls.encode_cwd(cwd)
        target = str(cwd)
        # id → (path, newest mtime, birth) — one id can appear under multiple dirs.
        found: dict[str, tuple[Path, float, datetime | None]] = {}
        for projects in cls.projects_dirs():
            folder = projects / encoded
            if not folder.is_dir():
                continue
            for path in folder.glob("*.jsonl"):
                session_id = path.stem
                if session_id == exclude_id or not path.is_file():
                    continue
                recorded_cwd, birth, _branch = cls._head_cwd_and_birth(path)
                if recorded_cwd != target:
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:  # best-effort: a vanished file just sorts oldest
                    mtime = 0.0
                prior = found.get(session_id)
                if prior is None or mtime > prior[1]:
                    found[session_id] = (path, mtime, birth)
        # Newest first (the running session); ties broken by id for a stable order.
        return [
            (sid, path, mtime, birth)
            for sid, (path, mtime, birth) in sorted(
                found.items(), key=lambda kv: (-kv[1][1], kv[0])
            )
        ]

    @classmethod
    def _first_cwd(cls, path: Path, *, max_lines: int = 200) -> str | None:
        """The ``cwd`` this session recorded, from the first line that carries one.

        Kept for callers that want only the cwd (``locate`` tie-breaking); a
        projection of :meth:`_head_cwd_and_birth`, whose docstring carries the
        preamble-scan rationale."""
        return cls._head_cwd_and_birth(path, max_lines=max_lines)[0]

    @staticmethod
    def _head_cwd_and_birth(
        path: Path, *, max_lines: int = 200
    ) -> tuple[str | None, datetime | None, str | None]:
        """The ``(cwd, birth, git_branch)`` this session recorded, from ONE
        bounded head read.

        Modern transcripts open with cwd-less preamble lines (``mode``,
        ``file-history-snapshot``, ``summary``); the ``cwd`` first appears a few
        lines in (the first ``attachment``/``user`` record) and the earliest
        timestamped record (records are time-sorted) is the session BIRTH.
        Line 0 never carries a cwd, so reading only that line yields ``None``
        for every real transcript, which silently breaks all cwd-based
        discovery and locate tie-breaking. All three facts ride out of one
        head read (bounded by ``max_lines`` so a pathological file costs no
        more than a head-read; all are near the top in practice), so the
        cheap adoption pre-filter never pays a full parse. ``git_branch``
        rides the SAME record as
        ``cwd`` (verified on-host: 374/374 real transcripts carry both on one
        line), so this costs no extra I/O over the ``(cwd, birth)`` shape the
        hot-path callers below already relied on — they simply ignore the third
        element.
        """
        first_cwd: str | None = None
        first_birth: datetime | None = None
        first_branch: str | None = None
        try:
            with path.open(encoding="utf-8") as fh:
                for index, line in enumerate(fh):
                    if index >= max_lines:
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        rec = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if first_cwd is None:
                        cwd = rec.get("cwd")
                        if isinstance(cwd, str) and cwd:
                            first_cwd = cwd
                    if first_branch is None:
                        branch = rec.get("gitBranch")
                        if isinstance(branch, str) and branch:
                            first_branch = branch
                    if first_birth is None:
                        first_birth = _parse_timestamp(rec.get("timestamp"))
                    if (
                        first_cwd is not None
                        and first_birth is not None
                        and first_branch is not None
                    ):
                        break
        except OSError:
            return (None, None, None)
        return (first_cwd, first_birth, first_branch)

    @classmethod
    def discover_all(cls) -> tuple[SessionRef, ...]:
        """Every session across EVERY folder in the projects cascade — the
        deliberately broader host-wide walk, distinct from :meth:`discover_paths`.

        ``discover_paths(cwd)`` jumps straight to the one forward-encoded
        folder for ``cwd`` (a single directory listing) because Claude's cwd
        encoding is a deterministic one-way function — that is what keeps it
        cheap enough for the 2 s activity poll, and this method must NOT be
        used to re-implement it (a host-wide walk on every poll tick would
        peg the daemon's CPU). This method exists only for
        catalog requests: it walks every ``<encoded-cwd>/`` folder under every
        ``projects_dirs()`` root and head-reads every top-level ``*.jsonl`` in
        each — the SAME bounded head read :meth:`discover_paths` already uses
        per file (``_head_cwd_and_birth``), just applied over every folder
        instead of one. Sub-agent files (``subagents/``) are skipped, exactly
        like ``discover_paths``. Best-effort: a malformed or vanished file is
        skipped, never raised; a file whose head read can't recover a cwd
        still yields a ref with ``cwd=None`` rather than being dropped.
        """
        found: dict[str, SessionRef] = {}
        for projects in cls.projects_dirs():
            for folder in projects.iterdir():
                if not folder.is_dir():
                    continue
                for path in folder.glob("*.jsonl"):
                    if not path.is_file():
                        continue
                    session_id = path.stem
                    cwd, birth, branch = cls._head_cwd_and_birth(path)
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    prior = found.get(session_id)
                    if prior is not None and prior.mtime >= mtime:
                        continue
                    found[session_id] = SessionRef(
                        session_id=session_id,
                        adapter_kind="claude_code",
                        cwd=cwd,
                        transcript_path=path,
                        birth=birth,
                        mtime=mtime,
                        git_branch=branch,
                    )
        return tuple(sorted(found.values(), key=lambda ref: (-ref.mtime, ref.session_id)))


class _ClaudeControls:
    """Resolves *which input controls* a Claude Code session in a cwd exposes.

    Pure filesystem enumeration: reads the worktree's ``.claude/``
    plus the user-level config-dir cascade, no running session needed. Every scan
    is best-effort — a missing dir, an unreadable file, or malformed JSON drops
    that source and never raises, exactly like :class:`_ClaudeHome`. Reads the
    environment live on each call so a relocated ``CLAUDE_CONFIG_DIR`` is picked
    up without a restart (the ``projects_dirs`` discipline).
    """

    # Bound the scan so a pathological tree (a symlink loop, a vendored node_modules
    # under .claude) can't turn a control panel read into an unbounded walk.
    _MAX_ENTRIES = 500

    #: The project-scoped MCP registry, committed at the repo root — so a Grove
    #: worktree inherits it, and a container reaches it through the workspace
    #: mount. Named once here because two callers resolve it now.
    PROJECT_MCP_FILENAME = ".mcp.json"

    @classmethod
    def scan(cls, cwd: Path) -> SessionControls:
        """The commands / skills / MCP-servers this session can invoke.

        Project scope is the worktree's own ``<cwd>/.claude`` + ``<cwd>/.mcp.json``
        (committed, so a Grove worktree inherits them); user scope is
        ``~/.claude`` (and the XDG config dir) — the same cascade Claude Code
        itself consults. Only the filesystem-scanned lists are filled; the model
        catalog and permission posture are config concerns the manager adds.
        """
        commands: list[SessionControl] = []
        skills: list[SessionControl] = []
        mcp_servers: list[SessionControl] = []
        seen_cmd: set[str] = set()
        seen_skill: set[str] = set()
        seen_mcp: set[str] = set()
        # (base .claude dir, scope) pairs — project worktree first, then the user
        # cascade; a name found earlier (project) wins, later dupes are skipped.
        bases: list[tuple[Path, ControlScope]] = [(cwd / ".claude", "project")]
        for cfg_dir in cls._user_config_dirs():
            bases.append((cfg_dir, "user"))
        for base, scope in bases:
            for ctrl in cls._scan_commands(base / "commands", scope):
                if ctrl.name not in seen_cmd:
                    seen_cmd.add(ctrl.name)
                    commands.append(ctrl)
            for ctrl in cls._scan_skills(base / "skills", scope):
                if ctrl.name not in seen_skill:
                    seen_skill.add(ctrl.name)
                    skills.append(ctrl)
        # MCP servers: the project ``.mcp.json`` (worktree-inherited, the
        # documented shared shape) then the user-global ``~/.claude.json`` — both
        # carry a top-level ``mcpServers`` object keyed by server name.
        mcp_files: list[tuple[Path, ControlScope]] = [
            (cwd / cls.PROJECT_MCP_FILENAME, "project"),
            (Path.home() / ".claude.json", "user"),
        ]
        for path, scope in mcp_files:
            for ctrl in cls._scan_mcp(path, scope):
                if ctrl.name not in seen_mcp:
                    seen_mcp.add(ctrl.name)
                    mcp_servers.append(ctrl)
        return SessionControls(
            commands=tuple(commands),
            skills=tuple(skills),
            mcp_servers=tuple(mcp_servers),
        )

    @staticmethod
    def _user_config_dirs() -> list[Path]:
        """The user-level config-dir bases the ``.claude/commands`` + ``skills``
        + ``.mcp.json`` cascade consults — the single cascade defined on
        ``_ClaudeHome.config_dirs`` (``CLAUDE_CONFIG_DIR`` → XDG → legacy home), so
        a relocated profile's controls are found the same way its transcripts
        are."""
        return _ClaudeHome.config_dirs()

    @classmethod
    def _scan_commands(cls, root: Path, scope: ControlScope) -> list[SessionControl]:
        """``*.md`` under a ``commands/`` dir → slash commands. Nested dirs are a
        namespace (``git/commit.md`` → ``git:commit``), matching Claude Code's own
        namespacing so the enumerated name is exactly what ``/name`` invokes."""
        if not root.is_dir():
            return []
        out: list[SessionControl] = []
        try:
            for path in sorted(root.rglob("*.md"))[: cls._MAX_ENTRIES]:
                if not path.is_file():
                    continue
                rel = path.relative_to(root).with_suffix("")
                name = ":".join(rel.parts)
                out.append(SessionControl(name=name, scope=scope, detail=cls._front_matter(path)))
        except OSError:
            return out
        return out

    @classmethod
    def _scan_skills(cls, root: Path, scope: ControlScope) -> list[SessionControl]:
        """Each ``skills/<name>/SKILL.md`` → one skill named for its dir."""
        if not root.is_dir():
            return []
        out: list[SessionControl] = []
        try:
            for skill_md in sorted(root.glob("*/SKILL.md"))[: cls._MAX_ENTRIES]:
                if not skill_md.is_file():
                    continue
                name = skill_md.parent.name
                out.append(
                    SessionControl(name=name, scope=scope, detail=cls._front_matter(skill_md))
                )
        except OSError:
            return out
        return out

    @classmethod
    def mcp_server_names(cls, path: Path) -> tuple[str, ...]:
        """The ``mcpServers`` object keys of an ``.mcp.json``-shaped file.

        The primitive under :meth:`_scan_mcp`, public because a second caller
        needs the bare names rather than controls: the container trust stamp
        pre-approves exactly the project-scoped servers this returns, and a
        second parser for one JSON object is how the two lists come to disagree.
        Best-effort like every scan here — an absent or malformed file is ``()``.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        servers = raw.get("mcpServers") if isinstance(raw, dict) else None
        if not isinstance(servers, dict):
            return ()
        return tuple(str(name) for name in list(servers)[: cls._MAX_ENTRIES])

    @classmethod
    def _scan_mcp(cls, path: Path, scope: ControlScope) -> list[SessionControl]:
        """Those server names as scoped controls."""
        return [SessionControl(name=name, scope=scope) for name in cls.mcp_server_names(path)]

    @staticmethod
    def _front_matter(path: Path) -> str | None:
        """A one-line ``description:`` from a command/skill's YAML front-matter, or
        ``None``. A best-effort head read (front-matter is at the top), so a big
        body never costs a full read; malformed input just yields ``None``."""
        try:
            with path.open(encoding="utf-8") as fh:
                head = fh.read(2048)
        except OSError:
            return None
        for line in head.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("description:"):
                value = stripped.split(":", 1)[1].strip().strip("\"'")
                return value or None
        return None


@dataclass(slots=True, frozen=True)
class _Record:
    """One transcript line, wrapped so every classification rule has one home.

    Holds the raw ``dict`` (heterogeneous external JSON, narrowed only through
    these typed accessors) plus the parse index for a stable sort tiebreak. The
    raw field stays ``Any``-typed on purpose — it's exactly the "genuinely
    heterogeneous external data, narrowed at the boundary" escape hatch.
    """

    raw: dict[str, Any]
    index: int

    # ── identity ──────────────────────────────────────────────────────────
    @property
    def type(self) -> str:
        value = self.raw.get("type")
        return value if isinstance(value, str) else ""

    @property
    def uuid(self) -> str | None:
        value = self.raw.get("uuid")
        return value if isinstance(value, str) else None

    @property
    def timestamp(self) -> datetime | None:
        return _parse_timestamp(self.raw.get("timestamp"))

    @property
    def is_sidechain(self) -> bool:
        return _as_bool(self.raw.get("isSidechain"))

    @property
    def is_meta(self) -> bool:
        return _as_bool(self.raw.get("isMeta"))

    @property
    def _message(self) -> dict[str, Any]:
        msg = self.raw.get("message")
        return msg if isinstance(msg, dict) else {}

    # ── content extraction ────────────────────────────────────────────────
    def _content_blocks(self) -> list[dict[str, Any]]:
        content = self._message.get("content")
        if isinstance(content, list):
            return [b for b in content if isinstance(b, dict)]
        return []

    def text(self) -> str:
        """Concatenated human-readable text (string content, or ``text`` blocks)."""
        content = self._message.get("content")
        if isinstance(content, str):
            return content
        parts = [
            block.get("text", "")
            for block in self._content_blocks()
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        ]
        return "\n".join(p for p in parts if p)

    def _has_block(self, block_type: str) -> bool:
        return any(b.get("type") == block_type for b in self._content_blocks())

    # ── classification ────────────────────────────────────────────────────
    @property
    def is_human_turn(self) -> bool:
        """A real user message — the filter that separates 5 turns from 48 lines.

        ``type:"user"``, not a sub-agent line, not meta, carrying no
        ``tool_result`` block, and whose text isn't a slash-command echo, bash
        I/O, caveat banner, or compaction summary.
        """
        if self.type != "user" or self.is_sidechain or self.is_meta:
            return False
        if _as_bool(self.raw.get("isCompactSummary")):
            return False
        if self._has_block("tool_result"):
            return False
        body = self.text()
        if not body.strip():
            return False
        return not any(marker in body for marker in _NON_HUMAN_MARKERS)

    @property
    def is_assistant(self) -> bool:
        """A main-thread assistant API response (one reply in the turn loop)."""
        return self.type == "assistant" and not self.is_sidechain

    @property
    def is_tool_result(self) -> bool:
        """A main-thread tool result (``type:"user"`` carrier, not a human turn)."""
        return self.type == "user" and not self.is_sidechain and self._has_block("tool_result")

    @property
    def is_task_notification(self) -> bool:
        """A delivered background-task notice (``type:"user"`` carrier of the
        ``<task-notification>`` envelope) — the agent being told a sub-agent or
        background command finished. A notification the agent *received*, so it
        advances the tail (the agent's move) but is never a human turn."""
        return (
            self.type == "user"
            and not self.is_sidechain
            and not self._has_block("tool_result")
            and _NOTIFICATION_MARKER in self.text()
        )

    def notification_text(self) -> str:
        """The notice, human-readable: the envelope's ``<summary>`` plus result.

        Falls back to a status line built from the envelope fields so a shape
        drift never yields raw XML — degraded text beats leaked markup.
        """
        body = self.text()
        summary = self._xml_field(body, "summary")
        if not summary:
            status = self._xml_field(body, "status") or "update"
            summary = f"background task {self._xml_field(body, 'task-id') or '?'}: {status}"
        result = self._xml_field(body, "result")
        return f"{summary}\n{result}" if result else summary

    def notification_tool_use_id(self) -> str | None:
        """The spawning ``tool_use`` id this notice closes (in-flight tracking)."""
        return self._xml_field(self.text(), "tool-use-id")

    @staticmethod
    def _xml_field(body: str, tag: str) -> str | None:
        match = re.search(rf"<{tag}>(.*?)</{tag}>", body, re.DOTALL)
        return match.group(1).strip() if match else None

    @property
    def is_teammate_message(self) -> bool:
        """A relayed message from a peer teammate session — the
        completion signal for a named (in-process-teammate) Agent
        spawn, a plain ``type:"user"`` STRING-content carrier (same shape
        :attr:`is_task_notification` guards against) wrapping
        ``<teammate-message teammate_id="...">…</teammate-message>``. A
        notice the agent *received*, so it advances the tail exactly like a
        task-notification, but is never a human turn."""
        return (
            self.type == "user"
            and not self.is_sidechain
            and not self._has_block("tool_result")
            and _TEAMMATE_MESSAGE_MARKER in self.text()
        )

    @property
    def is_agent_notice(self) -> bool:
        """Either received-notice shape that advances the tail without being
        a human turn: a ``<task-notification>`` or a peer
        ``<teammate-message>``. One combined predicate so the tail-loop
        dispatch (:meth:`_TranscriptParser.activity`) stays a single branch —
        ``_SubagentFleet.on_notice`` does the actual routing."""
        return self.is_task_notification or self.is_teammate_message

    def teammate_message_text(self) -> str:
        """The relayed notice, human-readable — never the raw
        ``<teammate-message>`` wrapper or CC's fixed disclaimer boilerplate
        around every relay.

        The wrapped body is either structured JSON (e.g.
        ``{"type":"idle_notification","from":...,"idleReason":...}``) or a
        peer's own plain-text message; both are handled, falling back
        gracefully rather than ever leaking markup.
        """
        sender = self.teammate_message_sender() or "a teammate"
        inner = self._teammate_message_inner()
        if inner is None:
            return f"message from {sender}"
        try:
            payload = json.loads(inner)
        except json.JSONDecodeError:
            return inner or f"message from {sender}"
        if not isinstance(payload, dict):
            return inner or f"message from {sender}"
        kind = payload.get("type")
        if kind == "idle_notification":
            reason = payload.get("idleReason") or "idle"
            return f"{sender} is now idle ({reason})"
        if isinstance(kind, str) and kind:
            return f"{sender}: {kind}"
        return inner or f"message from {sender}"

    def teammate_message_sender(self) -> str | None:
        """The sending teammate's name — the embedded JSON's ``from``, else
        the opening tag's ``teammate_id`` attribute."""
        payload = self._teammate_message_payload()
        if payload is not None:
            sender = payload.get("from")
            if isinstance(sender, str) and sender:
                return sender
        match = re.search(r'teammate_id="([^"]*)"', self.text())
        return match.group(1) if match else None

    @property
    def is_teammate_idle_notification(self) -> bool:
        """True only for the structured ``{"type":"idle_notification",...}``
        relay — the one teammate-message shape that means "this agent is done
        with its current work". Teammates also relay INTERIM messages while
        still running (progress reports, receipt acks, questions back to the
        lead); closing a spawn on those would undercount the live fleet, so
        the fleet close keys on this predicate, never on
        :attr:`is_teammate_message` alone."""
        payload = self._teammate_message_payload()
        return payload is not None and payload.get("type") == "idle_notification"

    def _teammate_message_payload(self) -> dict[str, Any] | None:
        """The embedded JSON payload of a ``<teammate-message>`` relay, or
        ``None`` when the body is plain prose / unparseable."""
        inner = self._teammate_message_inner()
        if inner is None:
            return None
        try:
            payload = json.loads(inner)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _teammate_message_inner(self) -> str | None:
        """The raw text between the ``<teammate-message>`` tags, stripped —
        JSON for a structured relay, plain prose for a peer's own message."""
        match = re.search(
            r"<teammate-message[^>]*>(.*?)</teammate-message>", self.text(), re.DOTALL
        )
        return match.group(1).strip() if match else None

    @property
    def tool_use_result(self) -> dict[str, Any]:
        """The top-level ``toolUseResult`` object CC writes on a tool_result
        line — metadata ABOUT the call (e.g. ``status``), distinct from
        ``message.content``'s ``tool_result`` block itself. ``{}`` when
        absent or not a dict (defensive — heterogeneous shape)."""
        value = self.raw.get("toolUseResult")
        return value if isinstance(value, dict) else {}

    @property
    def stop_reason(self) -> str | None:
        value = self._message.get("stop_reason")
        return value if isinstance(value, str) else None

    @property
    def model(self) -> str | None:
        value = self._message.get("model")
        if isinstance(value, str) and value and value != _SYNTHETIC_MODEL:
            return value
        return None

    @property
    def git_branch(self) -> str | None:
        value = self.raw.get("gitBranch")
        return value if isinstance(value, str) and value else None

    @property
    def tool_use_count(self) -> int:
        return sum(1 for b in self._content_blocks() if b.get("type") == "tool_use")

    # ── spine mapping ─────────────────────────────────────────────────────────
    def to_message(self) -> AgentMessage | None:
        """Map this record onto one agentic-loop spine message, or ``None`` for a
        record that is not a loop message (stream metadata, machinery, preamble).

        The role IS the classification (reusing the same predicates the status
        path trusts): a real human turn → ``user`` (its text as one block); a
        delivered ``<task-notification>`` OR a peer ``<teammate-message>``
        → ``notification`` (the cooked summary as text; the former's
        spawning tool id rides ``tool_use_id``, the latter carries none — it
        closes its spawn by name, not by tool-use id); a main-thread assistant
        reply → ``assistant``; a ``tool_result`` carrier → ``tool``. Sub-agent
        (sidechain) records ride with ``is_sidechain`` + their lineage
        (``parent_tool_use_id`` = ``sourceToolAssistantUUID``, ``thread_id`` =
        ``agentId``), classified by their underlying shape."""
        role = self._spine_role()
        if role is None:
            return None
        content: tuple[ContentBlock, ...]
        if role == "user":
            text = self.text()
            content = (ContentBlock(type="text", text=text),) if text.strip() else ()
        elif role == "notification":
            note = (
                self.teammate_message_text()
                if self.is_teammate_message
                else self.notification_text()
            )
            content = (ContentBlock(type="text", text=note),)
        else:
            content = self._map_blocks()
        return AgentMessage(
            role=role,
            content=content,
            message_id=self._message_id,
            tool_use_id=self.notification_tool_use_id() if role == "notification" else None,
            parent_tool_use_id=self._source_tool_uuid,
            model=self.model,
            usage=self._spine_usage(),
            timestamp=self.timestamp,
            is_sidechain=self.is_sidechain,
            thread_id=self._agent_id,
        )

    def _spine_role(self) -> MessageRole | None:
        # The main-thread predicates all exclude sidechain, so a sidechain record
        # only reaches ``_sidechain_role`` — where it keeps its real role plus the
        # ``is_sidechain`` flag, so lineage survives but projections can filter it.
        if self.is_human_turn:
            return "user"
        if self.is_agent_notice:
            return "notification"
        if self.is_assistant:
            return "assistant"
        if self.is_tool_result:
            return "tool"
        return self._sidechain_role() if self.is_sidechain else None

    def _sidechain_role(self) -> MessageRole | None:
        """A sub-agent (sidechain) record's role, from its underlying shape."""
        if self.type == "assistant":
            return "assistant"
        if self._has_block("tool_result"):
            return "tool"
        if self.type == "user" and self.text().strip():
            return "user"
        return None

    def _map_blocks(self) -> tuple[ContentBlock, ...]:
        """This record's raw content blocks as provider-neutral ContentBlocks.

        A ``tool_result``'s content is normalized to text the same way the
        answer map is (bare str / joined text blocks / ``None``), so a resolved
        question reads the identical answer whether sourced here or from the
        old record-level scan."""
        out: list[ContentBlock] = []
        for block in self._content_blocks():
            kind = block.get("type")
            if kind == "text":
                text = block.get("text")
                out.append(ContentBlock(type="text", text=text if isinstance(text, str) else None))
            elif kind == "thinking":
                text = block.get("thinking")
                out.append(
                    ContentBlock(type="thinking", text=text if isinstance(text, str) else None)
                )
            elif kind == "tool_use":
                name = block.get("name")
                raw_input = block.get("input")
                out.append(
                    ContentBlock(
                        type="tool_use",
                        tool_name=str(name) if name else None,
                        tool_use_id=str(block.get("id")) if block.get("id") else None,
                        tool_input=raw_input if isinstance(raw_input, dict) else None,
                    )
                )
            elif kind == "tool_result":
                out.append(
                    ContentBlock(
                        type="tool_result",
                        tool_use_id=(
                            str(block.get("tool_use_id")) if block.get("tool_use_id") else None
                        ),
                        text=self._result_text(block.get("content")),
                        is_error=_as_bool(block.get("is_error")),
                    )
                )
        return tuple(out)

    def _spine_usage(self) -> TokenUsage | None:
        """Per-message token usage, each field ``None`` when the record omitted it
        (never fabricated). Cache reads/writes stay SEPARATE here — the spine is
        faithful; the ``activity`` metrics fold them into ``tokens_in``."""
        usage = self._message.get("usage")
        if not isinstance(usage, dict):
            return None

        def _int(key: str) -> int | None:
            value = usage.get(key)
            return value if isinstance(value, int) else None

        return TokenUsage(
            input=_int("input_tokens"),
            output=_int("output_tokens"),
            cache_creation=_int("cache_creation_input_tokens"),
            cache_read=_int("cache_read_input_tokens"),
            reasoning=None,  # Claude's usage carries no reasoning-token count.
        )

    @property
    def _message_id(self) -> str | None:
        value = self._message.get("id")
        return value if isinstance(value, str) and value else None

    @property
    def _source_tool_uuid(self) -> str | None:
        """``sourceToolAssistantUUID`` — the assistant record carrying the
        tool_use that spawned this sub-agent thread (the on-disk lineage link,
        the streaming API's ``parent_tool_use_id``)."""
        value = self.raw.get("sourceToolAssistantUUID")
        return value if isinstance(value, str) and value else None

    @property
    def _agent_id(self) -> str | None:
        """``agentId`` — the sub-agent thread id (present only on sub-agent files)."""
        value = self.raw.get("agentId")
        return value if isinstance(value, str) and value else None

    def tool_names(self) -> list[str]:
        return [
            str(b.get("name"))
            for b in self._content_blocks()
            if b.get("type") == "tool_use" and b.get("name")
        ]

    def subagent_spawns(self) -> list[tuple[str, bool, str | None]]:
        """``(tool_use id, runs_in_background, teammate name)`` per sub-agent
        this record spawns.

        The background flag decides what closes the id: a foreground spawn ends
        with its ``tool_result``, but a backgrounded one gets an *immediate*
        launch-ack ``tool_result`` while the agent keeps running — only its
        later ``task-notification`` is the real return (verified on-host;
        closing on the ack read every background fleet as size 0). ``name`` is
        ``input.name`` when present (the in-process-teammate flavor,
        verified CC 2.1.209 — this flavor carries NO ``run_in_background`` at
        all, backgrounding is implicit) — the identity a later
        ``<teammate-message>`` completion line correlates against, since that
        line carries no tool-use id to match by.
        """
        out: list[tuple[str, bool, str | None]] = []
        for b in self._content_blocks():
            is_spawn = b.get("type") == "tool_use" and b.get("name") in _SUBAGENT_TOOLS
            if not is_spawn or not b.get("id"):
                continue
            raw_input = b.get("input") or {}
            name = raw_input.get("name")
            out.append(
                (
                    str(b.get("id")),
                    _as_bool(raw_input.get("run_in_background")),
                    name if isinstance(name, str) and name else None,
                )
            )
        return out

    def task_output_calls(self) -> list[tuple[str, str]]:
        """``(tool_use id, requested taskId)`` per ``TaskOutput`` poll call
        this record makes — the async-job id a Workflow spawn is closed by.
        A Workflow's own async id (``toolUseResult.taskId`` on its
        ``async_launched`` launch ack) is a DIFFERENT id space than the
        Task-board ``taskId`` TaskCreate/TaskUpdate use, but it IS the value
        ``TaskOutput`` polls with."""
        out: list[tuple[str, str]] = []
        for b in self._content_blocks():
            if b.get("type") == "tool_use" and b.get("name") == "TaskOutput" and b.get("id"):
                requested = (b.get("input") or {}).get("taskId")
                if isinstance(requested, str) and requested:
                    out.append((str(b.get("id")), requested))
        return out

    def tool_result_ids(self) -> list[str]:
        """``tool_use_id``s this record resolves (the call's return arriving)."""
        return [
            str(b.get("tool_use_id"))
            for b in self._content_blocks()
            if b.get("type") == "tool_result" and b.get("tool_use_id")
        ]

    @staticmethod
    def _result_text(content: Any) -> str | None:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                blk.get("text", "")
                for blk in content
                if isinstance(blk, dict)
                and blk.get("type") == "text"
                and isinstance(blk.get("text"), str)
            ]
            joined = "\n".join(p for p in parts if p)
            return joined or None
        return None

    @property
    def usage_tokens(self) -> tuple[int, int]:
        """``(input, output)`` token totals, cache reads/writes folded into input.

        Folding cache tokens into "in" reflects the true context size the user is
        paying to carry, which is the dashboard-relevant number — not just the
        fresh, uncached slice.
        """
        usage = self._message.get("usage")
        if not isinstance(usage, dict):
            return (0, 0)

        def _int(key: str) -> int:
            v = usage.get(key)
            return v if isinstance(v, int) else 0

        tokens_in = (
            _int("input_tokens")
            + _int("cache_read_input_tokens")
            + _int("cache_creation_input_tokens")
        )
        return (tokens_in, _int("output_tokens"))

    @property
    def ai_title(self) -> str | None:
        if self.type != "ai-title":
            return None
        value = self.raw.get("aiTitle")
        return value if isinstance(value, str) and value else None

    @property
    def last_prompt(self) -> str | None:
        if self.type != "last-prompt":
            return None
        value = self.raw.get("lastPrompt")
        return value if isinstance(value, str) and value else None

    @property
    def dedup_key(self) -> str:
        """Stable identity of the LOGICAL record (one API response = one key).

        Assistant lines key on ``message.id`` + ``requestId`` (ccusage's usage
        de-dup), so the split lines of one response collapse to one logical
        record and a re-emitted response from a forked file counts once. Other
        lines key on ``uuid``; with neither, the line is unique by parse index.
        """
        if self.type == "assistant":
            msg_id = self._message.get("id")
            req_id = self.raw.get("requestId")
            if isinstance(msg_id, str) and msg_id:
                return f"a:{msg_id}:{req_id if isinstance(req_id, str) else ''}"
        if self.uuid:
            return f"u:{self.uuid}"
        return f"i:{self.index}"

    def absorb_continuation(self, other: _Record) -> None:
        """Fold a same-message sibling line's content blocks into this record.

        Claude Code 2.x writes **one JSONL line per content block** — a single
        API response (``thinking`` → ``text`` → ``tool_use``…) arrives as N
        lines sharing one ``(message.id, requestId)`` with distinct ``uuid``s
        (verified on-host 2026-06-11: 123/123 multi-line messages). Keeping only
        the first line (always the ``thinking`` block) silently dropped every
        text and tool_use follow-up from turns, digests, and tool counts.
        ``usage`` and ``stop_reason`` are byte-identical across siblings, so
        only content moves; token math still counts each response once.

        Mutates ``raw`` in place — the frozen dataclass pins field *bindings*,
        and ``raw`` is the documented heterogeneous-data escape hatch.
        """
        mine = self._message.get("content")
        theirs = other._message.get("content")
        if isinstance(mine, list) and isinstance(theirs, list):
            mine.extend(b for b in theirs if isinstance(b, dict))


class _SubagentFleet:
    """Tracks spawned-but-unreturned sub-agents across one record stream.

    The closing rule differs by spawn mode (see ``_Record.subagent_spawns``):
    a foreground id closes on its ``tool_result``; a backgrounded id survives
    its immediate launch-ack result and closes only on a later signal —
    ``<task-notification>`` (the ``run_in_background`` flag), a
    ``<teammate-message>`` matched by name (in-process teammate), or a
    ``TaskOutput`` poll matched by async job id (Workflow run).
    """

    __slots__ = (
        "_background",
        "_in_flight",
        "_pending_task_output",
        "_teammate_names",
        "_workflow_task_ids",
    )

    def __init__(self) -> None:
        self._in_flight: set[str] = set()
        self._background: set[str] = set()
        # spawn id → the identity/correlation a later close signal matches on.
        self._teammate_names: dict[str, str] = {}
        self._workflow_task_ids: dict[str, str] = {}
        # TaskOutput's own call id → the Workflow taskId it asked about.
        self._pending_task_output: dict[str, str] = {}

    def spawn(self, rec: _Record) -> None:
        for spawn_id, in_background, name in rec.subagent_spawns():
            self._in_flight.add(spawn_id)
            if in_background:
                self._background.add(spawn_id)
            if name:
                self._teammate_names[spawn_id] = name
        for call_id, task_id in rec.task_output_calls():
            self._pending_task_output[call_id] = task_id

    def on_tool_result(self, rec: _Record) -> None:
        result_ids = set(rec.tool_result_ids())
        ack = rec.tool_use_result
        status = ack.get("status")
        if status in _ASYNC_ACK_STATUSES:
            # A launch ack (in-process-teammate mailbox, or a Workflow run),
            # never the real return — needs a later external close signal,
            # exactly like the legacy run_in_background flag.
            pending = result_ids & self._in_flight
            self._background.update(pending)
            name = ack.get("name")
            if isinstance(name, str) and name:
                for spawn_id in pending:
                    self._teammate_names.setdefault(spawn_id, name)
            if status == "async_launched":
                task_id = ack.get("taskId")
                if isinstance(task_id, str) and task_id:
                    for spawn_id in pending:
                        self._workflow_task_ids[spawn_id] = task_id
        else:
            self._in_flight.difference_update(result_ids - self._background)
        # A TaskOutput poll's own result closes the Workflow spawn it asked
        # about, keyed by the async taskId from that spawn's launch ack —
        # "leave open" (no invented poll) is the honest default otherwise.
        for call_id in result_ids:
            requested = self._pending_task_output.pop(call_id, None)
            if requested is None:
                continue
            for spawn_id, task_id in list(self._workflow_task_ids.items()):
                if task_id == requested:
                    self._in_flight.discard(spawn_id)
                    self._background.discard(spawn_id)
                    del self._workflow_task_ids[spawn_id]

    def on_notice(self, rec: _Record) -> None:
        """Routes a received notice (:attr:`_Record.is_agent_notice`) to its
        matching close rule — the single call site :meth:`activity` uses so
        the tail-loop dispatch stays one branch."""
        if rec.is_task_notification:
            self.on_notification(rec)
        else:
            self.on_teammate_message(rec)

    def on_notification(self, rec: _Record) -> None:
        self._in_flight.discard(rec.notification_tool_use_id() or "")

    def on_teammate_message(self, rec: _Record) -> None:
        """Closes the matching in-process-teammate spawn on its IDLE
        notification — the name-matched counterpart of
        :meth:`on_notification`'s tool-use-id match, since a
        ``<teammate-message>`` carries no machine-readable tool-use id.
        Only the structured idle_notification closes: an interim
        relay (progress report, receipt ack) means the teammate is still
        running, and an idle notification always follows the real finish."""
        if not rec.is_teammate_idle_notification:
            return
        sender = rec.teammate_message_sender()
        if not sender:
            return
        for spawn_id, name in list(self._teammate_names.items()):
            if spawn_id in self._in_flight and self._names_match(name, sender):
                self._in_flight.discard(spawn_id)
                self._background.discard(spawn_id)

    @staticmethod
    def _names_match(remembered: str, sender: str) -> bool:
        """Bare-name equality, ignoring an optional ``@<team>`` suffix either
        side may or may not carry — the spawn's ``input.name`` is bare; a
        launch ack's ``name``/``agent_id`` and a completion line's ``from``
        have both been observed bare and ``name@team``-qualified."""
        return remembered.split("@", 1)[0] == sender.split("@", 1)[0]

    @property
    def active(self) -> int:
        return len(self._in_flight)


class _TranscriptParser:
    """Aggregates de-duplicated, time-sorted records into one :class:`AgentActivity`.

    Single pass. Owns the per-turn bucketing that yields ``replies_per_turn`` and
    the tail-status rule. Constructed from already-read records so it stays pure
    and unit-testable without touching the filesystem.
    """

    def __init__(self, records: Sequence[_Record]) -> None:
        self._records = records

    def activity(self) -> AgentActivity:
        if not self._records:
            return AgentActivity.empty(AgentActivityState.UNKNOWN)

        buckets: list[int] = []
        tool_calls = 0
        tokens_in = 0
        tokens_out = 0
        model: str | None = None
        title: str | None = None
        last_event_at: datetime | None = None
        # The last record that is a human turn or an assistant reply — the tail
        # the status rule reads. Side records (titles, attachments) don't move it.
        tail: _Record | None = None
        fleet = _SubagentFleet()

        for rec in self._records:
            ts = rec.timestamp
            if ts is not None and (last_event_at is None or ts > last_event_at):
                last_event_at = ts

            if rec.ai_title:
                title = rec.ai_title
                continue
            if rec.last_prompt:
                continue

            if rec.is_human_turn:
                buckets.append(0)
                tail = rec
            elif rec.is_assistant:
                if buckets:
                    buckets[-1] += 1
                tool_calls += rec.tool_use_count
                fleet.spawn(rec)
                t_in, t_out = rec.usage_tokens
                tokens_in += t_in
                tokens_out += t_out
                model = rec.model or model
                tail = rec
            elif rec.is_agent_notice:
                # The agent was just told a background task finished, or a
                # peer teammate relayed a message (often idle/completion)
                # → its move either way; `on_notice` routes the close.
                fleet.on_notice(rec)
                tail = rec
            elif rec.is_tool_result:
                # A tool just returned → it's the agent's move. Without this the
                # tail stays the PREVIOUS assistant record through the whole tool
                # run, and its stop_reason can mis-report a busy session (one
                # whole turn of status lag).
                fleet.on_tool_result(rec)
                tail = rec

        # The task text comes from ONE selection helper shared with
        # `current_task_text()`, so the capped field on this ~1 Hz-delivered
        # activity and the uncapped per-request read can never name different
        # text. The cap applies to BOTH branches of that selection: a
        # `last-prompt` record carries a whole pasted prompt verbatim, and
        # putting that on the poll path is precisely what the cap prevents.
        raw_task = self.current_task_text()
        current_task = _truncate(raw_task, _TASK_TEXT_CAP) if raw_task is not None else None

        state = self._tail_state(tail)
        if state is AgentActivityState.WAITING and fleet.active > 0:
            # A backgrounded Agent/Task spawn can close the orchestrator's OWN
            # turn (tail stop_reason == end_turn) while its sidechain fleet
            # keeps working — a session whose fleet is running IS working, so
            # a bare tail-derived WAITING is the wrong read here. BLOCKED (and
            # ERROR, were it ever tail-derived) are never promoted: an
            # unanswered question needs the human regardless of the fleet.
            # No separate "is the fleet actually alive" check is needed — the
            # loop above already folds sidechain timestamps into
            # `last_event_at` (it scans every record in `self._records`, main
            # + sub-agent), so a fleet that dies mid-run goes stale and
            # `ActivityService._blend`'s existing freshness/settle rules are
            # the safety net that demotes a dead fleet back down.
            state = AgentActivityState.WORKING

        return AgentActivity(
            state=state,
            title=title,
            current_task=current_task,
            human_turns=len(buckets),
            assistant_replies=sum(buckets),
            replies_per_turn=tuple(buckets),
            tool_calls=tool_calls,
            active_subagents=fleet.active,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            last_event_at=last_event_at,
            started_at=self.created_at(),
        )

    def messages(self) -> tuple[AgentMessage, ...]:
        """The de-duplicated, time-sorted records mapped onto the agentic-loop
        spine — the ONE representation :meth:`turns` and :meth:`digest`
        below both project (DRY: one parse, many projections). Non-message
        records map to nothing; sub-agent (sidechain) messages ride with their
        lineage fields set."""
        return tuple(msg for rec in self._records if (msg := rec.to_message()) is not None)

    def digest(self) -> OrderedDigest:
        """Ordered ``user / assistant / tool`` skeleton, ``tool_result`` stripped
        — a projection of :meth:`messages`."""
        entries: list[DigestEntry] = []
        for message in self.messages():
            if message.is_sidechain:
                continue
            if message.role == "user":
                entries.append(DigestEntry("user", _truncate(message.text(), _DIGEST_TEXT_CAP)))
            elif message.role == "notification":
                note = _truncate(message.text(), _DIGEST_TEXT_CAP)
                entries.append(DigestEntry("notification", note))
            elif message.role == "assistant":
                names = message.tool_names()
                if names:
                    entries.append(DigestEntry("tool", ", ".join(names)))
                else:
                    text = _truncate(message.text(), _DIGEST_TEXT_CAP)
                    if text:
                        entries.append(DigestEntry("assistant", text))
            # role == "tool": a result carrier — excluded from the skeleton.
        return OrderedDigest(tuple(entries[-_DIGEST_MAX_ENTRIES:]))

    def turns(self, *, last: int | None = None) -> tuple[SessionTurn, ...]:
        """The conversation as :class:`SessionTurn` rows, oldest first — a
        projection of :meth:`messages`.

        Assistant messages that precede any human turn (a resumed or compacted
        session whose head was filtered out) collect under a leading turn with
        an empty ``user_text`` rather than being dropped — `sessions show`
        renders it as a continuation block. Sub-agent (sidechain) messages are
        lineage, not main-thread turns, so they are skipped here (the shared
        builder this delegates to is also how :meth:`ClaudeCodeAdapter.subagent_turns`
        renders ONE sidechain thread's own turns).
        """
        return self._turns_from_messages(self.messages(), last=last)

    @classmethod
    def _turns_from_messages(
        cls,
        messages: Sequence[AgentMessage],
        *,
        last: int | None = None,
        include_sidechain: bool = False,
    ) -> tuple[SessionTurn, ...]:
        """The one turn-builder behind :meth:`turns` (main thread,
        ``include_sidechain=False``) and :meth:`ClaudeCodeAdapter.subagent_turns`
        (one already-thread-filtered sidechain, ``include_sidechain=True``) —
        so a sub-agent's rendered turns (question/file-edit/todo structuring,
        leading-continuation handling) can never drift from the main thread's.
        """
        turns: list[SessionTurn] = []
        entries: list[DigestEntry] = []
        # Pre-scan every tool_result block so a question entry renders resolved
        # no matter where its answer landed (a tool_result is a forward reference
        # — it always follows the question's tool_use). Sidechain results are
        # included, exactly as the record-level scan was.
        answered: dict[str, str | None] = {}
        for message in messages:
            for block in message.content:
                if block.type == "tool_result" and block.tool_use_id:
                    answered[block.tool_use_id] = block.text

        # One board per parse (session-scoped, never persisted) — folds
        # TaskCreate/TaskUpdate calls into the running task list `answered`
        # above already carries each call's own tool_result, which is also
        # where a TaskCreate's server-assigned id lives (see `TaskBoard`).
        board = TaskBoard()

        def _flush(user_text: str, started_at: datetime | None) -> None:
            turns.append(
                SessionTurn(user_text=user_text, started_at=started_at, entries=tuple(entries))
            )
            entries.clear()

        current: tuple[str, datetime | None] | None = None
        for message in messages:
            if message.is_sidechain and not include_sidechain:
                continue
            if message.role == "user":
                if current is not None or entries:
                    _flush(*(current or ("", None)))
                current = (message.text(), message.timestamp)
            elif message.role == "notification":
                # A notice the agent received mid-turn — an entry inside the
                # current turn, never a new turn of its own.
                entries.append(DigestEntry("notification", message.text()))
            elif message.role == "assistant":
                if current is None and not entries and message.timestamp is not None:
                    # Leading continuation block inherits the first reply's time.
                    current = ("", message.timestamp)
                entries.extend(cls._assistant_entries(message, answered, board))
            # role == "tool": a result carrier — feeds `answered`, no entry.
        if current is not None or entries:
            _flush(*(current or ("", None)))

        if last is not None:
            return tuple(turns[-last:]) if last > 0 else ()
        return tuple(turns)

    @classmethod
    def _assistant_entries(
        cls, message: AgentMessage, answered: Mapping[str, str | None], board: TaskBoard
    ) -> list[DigestEntry]:
        """One assistant message's content projected to turn entries, in block
        order — text, structured questions, structured file edits, structured
        todo lists, and plain tool calls exactly as they appeared (the full-text
        `sessions show` view).

        A question tool (``AskUserQuestion`` / ``ExitPlanMode``) yields one
        ``question`` entry per question (a batch → N), stamped resolved when its
        group_id is in ``answered``; a file-edit tool yields one ``file_edit``
        per edit (a ``MultiEdit`` → N); a todo tool (``TodoWrite``) yields one
        ``todo`` entry carrying the whole list; a Task-system call
        (``TaskCreate``/``TaskUpdate``, see :data:`TASK_TOOL_NAMES`) folds into
        ``board`` and — when it actually changed anything — also yields one
        ``todo`` entry carrying the board's current snapshot, so the SAME pinned
        card TodoWrite feeds renders the Task system too; any other tool keeps
        its single ``tool`` entry — as does a recognized edit/todo/task call
        whose payload didn't parse or apply. ``thinking`` blocks are dropped (the
        render never showed them)."""
        entries: list[DigestEntry] = []
        for block in message.content:
            if block.type == "text":
                if block.text and block.text.strip():
                    entries.append(DigestEntry("assistant", block.text))
            elif block.type == "tool_use" and block.tool_name:
                name = block.tool_name
                if AgentQuestion.recognizes(name):
                    # ``tool_use_id or ""``: an id-less block yields a group_id
                    # that simply never matches the answered map, never "None".
                    for q in AgentQuestion.from_tool_call(
                        name, block.tool_input, block.tool_use_id or ""
                    ):
                        resolved = q.resolved(answered[q.group_id]) if q.group_id in answered else q
                        entries.append(DigestEntry("question", resolved.prompt, question=resolved))
                elif FileEdit.recognizes(name) and (
                    edits := FileEdit.from_tool_call(name, block.tool_input)
                ):
                    entries.extend(
                        DigestEntry("file_edit", f"{name} {edit.path}".strip(), file_edit=edit)
                        for edit in edits
                    )
                elif TodoList.recognizes(name) and (
                    todos := TodoList.from_tool_call(name, block.tool_input)
                ):
                    entries.extend(DigestEntry("todo", lst.summary, todo=lst) for lst in todos)
                elif name in TASK_TOOL_NAMES and board.apply(name, block, answered):
                    snapshot = board.snapshot()
                    entries.append(DigestEntry("todo", snapshot.summary, todo=snapshot))
                else:
                    entries.append(DigestEntry("tool", cls._tool_display(name, block.tool_input)))
        return entries

    @staticmethod
    def _tool_display(name: str, tool_input: dict[str, Any] | None) -> str:
        """One tool call as a display line — bare name for most tools, but a
        spawn tool (``Agent`` / ``Task``) carries the sub-agent type + description
        clients need (a bare ``Agent`` row hid the whole fleet). Question and
        file-edit tools never reach here — :meth:`_assistant_entries` routes them
        to structured entries first."""
        if not isinstance(tool_input, dict):
            return name
        if name in _SUBAGENT_TOOLS:
            agent_type = tool_input.get("subagent_type") or "agent"
            description = tool_input.get("description") or ""
            label = f"{name}({agent_type})"
            return f"{label}: {description}" if description else label
        return name

    def first_human_text(self) -> str | None:
        """The first real prompt, truncated — the SDK's ``first_prompt`` analogue."""
        return self._first_human_text()

    def last_prompt_text(self) -> str | None:
        """The newest ``last-prompt`` record that actually carries text.

        Some ``last-prompt`` records are leafUuid-only pointers (verified
        on-host) — those are skipped, not treated as an empty prompt.
        """
        for rec in reversed(self._records):
            if rec.last_prompt:
                return rec.last_prompt
        return None

    def current_task_text(self) -> str | None:
        """The session's task text, UNCAPPED — the ONE selection
        :meth:`activity` caps onto ``AgentActivity.current_task``.

        The rule (unchanged, only factored out): the newest ``last-prompt``
        record carrying text wins, else the FIRST real human turn's text. Both
        readers share this method precisely so a future change to the rule
        cannot move one and leave the other behind. Whitespace-only text is
        ``None`` — the honest "this session carries no task text".
        """
        text = self.last_prompt_text() or self._first_human_raw()
        return text if text and text.strip() else None

    def created_at(self) -> datetime | None:
        """Timestamp of the earliest timestamped record (records are time-sorted)."""
        for rec in self._records:
            if rec.timestamp is not None:
                return rec.timestamp
        return None

    def git_branch(self) -> str | None:
        """The branch the session first recorded (records carry ``gitBranch``)."""
        for rec in self._records:
            if rec.git_branch:
                return rec.git_branch
        return None

    def recorded_cwd(self) -> str | None:
        """The working directory the session recorded (first record carrying one)."""
        for rec in self._records:
            cwd = rec.raw.get("cwd")
            if isinstance(cwd, str) and cwd:
                return cwd
        return None

    def _first_human_text(self) -> str | None:
        raw = self._first_human_raw()
        return _truncate(raw, _TASK_TEXT_CAP) if raw is not None else None

    def _first_human_raw(self) -> str | None:
        for rec in self._records:
            if rec.is_human_turn:
                return rec.text()
        return None

    @staticmethod
    def _tail_state(tail: _Record | None) -> AgentActivityState:
        """Transcript-only status from the tail (epic §6).

        A human turn at the tail → WORKING (the agent's turn to respond). An
        assistant tail → WORKING while in the tool loop or mid-stream
        (``tool_use`` / no ``stop_reason``), WAITING once the turn closes
        (``end_turn`` / ``stop_sequence``). The IDLE refinement (tmux quiet) and
        STARTING (no file) are the ``ActivityService``'s blend, not the
        transcript's call.
        """
        if tail is None:
            return AgentActivityState.UNKNOWN
        if tail.is_human_turn or tail.is_agent_notice:
            return AgentActivityState.WORKING
        if tail.stop_reason in ("end_turn", "stop_sequence"):
            return AgentActivityState.WAITING
        # An assistant tail still holding an unanswered ask-the-human call is
        # action-required, not "working": the question/plan-approval prompt is
        # the one transcript-visible BLOCKED signal (permission prompts need the
        # hook sidecar — they never reach the JSONL).
        if any(AgentQuestion.recognizes(name) for name in tail.tool_names()):
            return AgentActivityState.BLOCKED
        return AgentActivityState.WORKING


def _truncate(text: str, cap: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _digit_for(index: int, q: AgentQuestion) -> str:
    """The option digit (1-based) for a 0-based option ``index``, range-checked.

    The TUI numbers predefined options from 1; ``build_answer_keys`` types these.
    Raises ``ValueError`` for an index outside the question's real options so a
    bad plan is rejected (422) rather than driving a wrong or out-of-bounds key.
    """
    if not 0 <= index < len(q.options):
        raise ValueError(
            f"option index {index} out of range for a question with {len(q.options)} option(s)"
        )
    return str(index + 1)


class ClaudeCodeAdapter:
    """Introspect Claude Code sessions (the first concrete :class:`AgentAdapter`).

    Stateless: every method is read-only over the filesystem or pure, so one
    shared instance serves every workspace. Filesystem reads are funnelled
    through :class:`_ClaudeHome`; all parsing through :class:`_TranscriptParser`.
    """

    kind = "claude_code"
    remote = False
    resumable = True

    def launch_decoration(self, session_id: str, *, resume: bool = False) -> list[str]:
        """``--session-id <uuid>`` for a fresh session — what makes correlation
        deterministic — or ``--resume <uuid>`` to CONTINUE an existing one.
        Plain ``--resume`` keeps the same session id/file (it does NOT
        rotate the id — that needs ``--fork-session``), so pinning
        ``agent_session_id`` to the resumed id stays correct by construction."""
        if resume:
            return ["--resume", session_id]
        return ["--session-id", session_id]

    def model_decoration(self, model: str) -> list[str]:
        """``--model <id>`` — Claude Code's per-launch model selector."""
        return ["--model", model]

    def offline_decoration(self) -> list[str]:
        """``--disallowedTools WebFetch,WebSearch`` — Claude Code's flag for
        dropping the two network-facing built-in tools."""
        return ["--disallowedTools", "WebFetch,WebSearch"]

    def telemetry_env(self) -> dict[str, str]:
        """Claude Code's native-telemetry switch: the master
        enable plus the OTLP exporter selection for its metrics + logs. The
        OTLP *endpoint/headers* ride in from ``TelemetryConfig.derive_env``, so
        with all three present Claude Code streams its own token-usage/cost/tool
        metrics and ``api_request`` events straight to LangFuse. Spans (TTFT) are
        a beta opt-in — set ``OTEL_TRACES_EXPORTER=otlp`` +
        ``CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`` via the agent's ``env`` — kept
        out of the default because the beta schema can shift between releases."""
        return {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "OTEL_METRICS_EXPORTER": "otlp",
            "OTEL_LOGS_EXPORTER": "otlp",
        }

    # Claude Code exposes NO CLI model enumeration — model discovery is the
    # interactive ``/model`` picker only (there is no ``claude --list-models``).
    # Its tier ALIASES, however, are the stable public ``--model`` vocabulary:
    # each re-points to the current model for that tier every release (a new
    # Opus keeps ``opus``), so offering them never goes stale the way a dated
    # id would — exactly the "don't hard-code model ids" goal. A full id or a
    # gateway model still works (forwarded verbatim); a deployment that wants a
    # different set pins ``AgentSpec.models`` in config, which the resolver
    # prefers over this default.
    _MODEL_ALIASES: tuple[str, ...] = ("sonnet", "opus", "haiku")

    def available_models(self, command: str) -> tuple[str, ...]:
        """Claude Code's stable tier aliases (``sonnet``/``opus``/``haiku``).

        Not read from the CLI (none enumerates models); the aliases are the
        provider's durable ``--model`` vocabulary, so this is a mechanism
        default, not a hard-coded dated id. Overridden by ``AgentSpec.models``.
        """
        del command
        return self._MODEL_ALIASES

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        try:
            return _ClaudeHome.locate(cwd, session_id)
        except OSError as exc:  # best-effort: a glob failure must not break peek
            logger.debug("locate_transcripts({}, {}) failed: {}", cwd, session_id, exc)
            return []

    def discover_sessions(self, cwd: Path, *, exclude_id: str | None = None) -> list[str]:
        """Session ids of transcripts whose recorded cwd is ``cwd`` but that Grove
        didn't launch (out-of-band discovery).

        Scans only the forward-encoded candidate folder per config dir (one
        directory listing — bounded), and confirms each by the in-line ``cwd``
        rather than trusting the lossy folder name. Excludes ``exclude_id`` (the
        Grove-launched session) so only the user's hand-started ``claude`` runs
        surface. Best-effort: returns ``[]`` on any error.
        """
        try:
            return _ClaudeHome.discover(cwd, exclude_id=exclude_id)
        except OSError as exc:
            logger.debug("discover_sessions({}) failed: {}", cwd, exc)
            return []

    def discover_births(
        self, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, datetime | None, float]]:
        """``(session_id, birth, mtime)`` for discovered sessions — the cheap
        adoption pre-filter. Birth rides out of the same bounded head read
        ``discover_sessions`` already does; no full transcript parse. Best-effort:
        ``[]`` on any error."""
        try:
            return [
                (sid, birth, mtime)
                for sid, _path, mtime, birth in _ClaudeHome.discover_paths(
                    cwd, exclude_id=exclude_id
                )
            ]
        except OSError as exc:
            logger.debug("discover_births({}) failed: {}", cwd, exc)
            return []

    def discover_all(self) -> tuple[SessionRef, ...]:
        """Every session across every folder in the projects cascade — the
        host-wide catalog scan (never the 2 s poll; see
        ``_ClaudeHome.discover_all`` for why this must stay separate from
        ``discover_paths``). Best-effort: ``()`` on any error."""
        try:
            return _ClaudeHome.discover_all()
        except OSError as exc:
            logger.debug("discover_all() failed: {}", exc)
            return ()

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        """Normalized summaries for every session recorded in ``cwd``, newest-first.

        One full parse per main transcript (sub-agent files are excluded from
        the summary scope — they describe sidechains, not the session). The
        same parse yields both the listing metadata and the point-in-time
        ``activity``, so a listing never reads a file twice. Best-effort:
        a session that fails to read still lists with empty metadata.
        """
        try:
            scanned = _ClaudeHome.discover_paths(cwd)
        except OSError as exc:
            logger.debug("list_sessions({}) failed: {}", cwd, exc)
            return []
        return [self._summarize(sid, path, mtime) for sid, path, mtime, _ in scanned]

    def read_turns(
        self, cwd: Path, session_id: str, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("turns", str(cwd), session_id, last),
            paths,
            lambda: _TranscriptParser(self._read(paths)).turns(last=last),
        )

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        """The session's agentic-loop spine — the lineage-preserving
        message list ``read_turns`` / ``transcript_digest`` project from, and the
        seam downstream fleet / trace / final-result consumers read. Reads the
        main + sub-agent transcripts, so sub-agent messages ride with their
        ``is_sidechain`` / ``parent_tool_use_id`` / ``thread_id`` lineage set."""
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("messages", str(cwd), session_id),
            paths,
            lambda: _TranscriptParser(self._read(paths)).messages(),
        )

    def final_result(self, cwd: Path, session_id: str) -> FinalResult | None:
        """The session's terminal outcome — a projection of
        :meth:`read_messages`, never a second parser."""
        return final_result_from_messages(self.read_messages(cwd, session_id))

    def latest_todo(self, cwd: Path, session_id: str) -> TodoList | None:
        """The session's current todo/checklist state — a projection of
        :meth:`read_messages`, never a second parser."""
        return latest_todo_from_messages(self.read_messages(cwd, session_id))

    def latest_task(self, cwd: Path, session_id: str) -> str | None:
        """The session's task text, uncapped — the same
        :meth:`_TranscriptParser.current_task_text` selection
        :meth:`parse_activity` caps onto ``AgentActivity.current_task``.

        Rides the same incremental record read + stat-signature memo every
        other projection here does, so it costs a ``stat`` on an unchanged
        transcript and only the appended bytes on a live one.
        """
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("task", str(cwd), session_id),
            paths,
            lambda: _TranscriptParser(self._read(paths)).current_task_text(),
        )

    @staticmethod
    def project_mcp_servers(cwd: Path) -> tuple[str, ...]:
        """Server names the worktree's committed ``.mcp.json`` registers.

        A read-only projection of the same scan :meth:`session_controls` uses,
        surfaced publicly (rather than left inside ``_ClaudeControls``) because
        the container trust stamp pre-approves this exact list before the
        container starts — a per-folder approval nobody is there to give
        interactively. The USER-scoped registry is deliberately not included:
        it names host commands a container cannot run, which is why the seed
        drops it wholesale. Deliberately NOT on ``AgentAdapter`` — that seam is
        the normalized session-read surface, and this is a launch-time fact
        about one tool's project config.
        """
        return _ClaudeControls.mcp_server_names(cwd / _ClaudeControls.PROJECT_MCP_FILENAME)

    def session_controls(self, cwd: Path, session_id: str) -> SessionControls:
        """Enumerate the session's input controls — a filesystem scan.

        Delegates the whole scan to :class:`_ClaudeControls` (all the ``.claude``
        path logic has one home, like ``_ClaudeHome`` for transcripts). Fills only
        the filesystem-scanned lists; the model catalog / permission posture are
        the manager's to add. ``session_id`` is unused — the controls surface is a
        property of the worktree, not the specific session — but stays in the
        signature per the seam contract. Best-effort: an empty surface on any
        error, never raises."""
        del session_id
        try:
            return _ClaudeControls.scan(cwd)
        except OSError as exc:  # best-effort: a scan hiccup must not break the panel
            logger.debug("session_controls({}) failed: {}", cwd, exc)
            return SessionControls.empty()

    # ── fleet reader ──────────────────────────────────────────────────────────
    def fleet_activity(
        self, cwd: Path, session_id: str
    ) -> list[tuple[AgentSession, AgentActivity]]:
        """Per-sub-agent identity + point-in-time activity for this session's
        in-session fleet — the itemized counterpart to the bare
        ``AgentActivity.active_subagents`` COUNT (unchanged; still derived by
        ``_SubagentFleet`` inside :meth:`parse_activity`, whose foreground-vs-
        background closing rule this does not touch).

        Builds on the already-normalized spine (:meth:`read_messages`) —
        never a second transcript re-read. A sidechain ``AgentMessage`` already
        carries ``thread_id`` (Claude's ``agentId``); grouping by it recovers
        one worker's own ordered turns. Each thread's own tail then gives its
        status — the ``AgentMessage`` analogue of the main thread's tail rule,
        read off content-block SHAPE (an assistant reply ending in a
        ``tool_use`` block is exactly when Claude's own ``stop_reason`` would
        read ``"tool_use"``) since the spine deliberately omits the raw
        ``stop_reason``. Identity comes from the sidecar ``.meta.json``
        (:meth:`_ClaudeHome.read_subagent_meta`), preferring ``name`` then
        ``agentType`` then a truncated first-task-prompt fallback (a Workflow
        worker's sparse meta carries neither name nor description),
        never fabricated. Only the in-session sidechain fleet is covered
        here; a CLI ``--bg`` background session is a separate top-level
        session Grove would track like any other, not a sub-agent thread.

        Returns ``()`` when the session spawned no sub-agents (no
        ``subagents/`` dir anywhere under it — the glob is recursive,
        so a Workflow worker nested under ``subagents/workflows/wf_<id>/``
        is covered too) — the common case, cheap: no message read is paid.
        """
        subagent_paths = {
            p.stem: p for p in self.locate_transcripts(cwd, session_id) if "subagents" in p.parts
        }
        if not subagent_paths:
            return []
        by_thread: dict[str, list[AgentMessage]] = {}
        for msg in self.read_messages(cwd, session_id):
            if msg.is_sidechain and msg.thread_id:
                by_thread.setdefault(msg.thread_id, []).append(msg)

        out: list[tuple[AgentSession, AgentActivity]] = []
        for thread_id, thread_messages in by_thread.items():
            if not thread_messages:
                continue
            path = subagent_paths.get(f"agent-{thread_id}")
            meta = _ClaudeHome.read_subagent_meta(path) if path is not None else {}
            # Identity prefers the teammate's own chosen `name` over
            # `agentType` — a Workflow worker's sparse meta carries neither,
            # degrading further to `_fallback_description` below.
            name = meta.get("name")
            agent_type = meta.get("agentType")
            title = name if isinstance(name, str) and name else None
            if title is None and isinstance(agent_type, str) and agent_type:
                title = agent_type
            description = meta.get("description")
            out.append(
                (
                    AgentSession(
                        session_id=thread_id,
                        transcript_path=path,
                        adapter_kind=self.kind,
                        provenance="fs_discovered",
                        tmux_window=None,
                        parent_session_id=session_id,
                    ),
                    self._fleet_thread_activity(
                        thread_messages,
                        title=title,
                        current_task=(
                            description
                            if isinstance(description, str) and description
                            else self._fallback_description(thread_messages)
                        ),
                    ),
                )
            )
        return out

    @classmethod
    def _fleet_thread_activity(
        cls, messages: Sequence[AgentMessage], *, title: str | None, current_task: str | None
    ) -> AgentActivity:
        """One sub-agent's own metrics, folded from its messages in the exact
        shape :meth:`_TranscriptParser.activity` folds the main thread's — same
        cache-into-``tokens_in`` convention, same last-non-empty-wins ``model``."""
        human_turns = 0
        assistant_replies = 0
        tool_calls = 0
        model: str | None = None
        tokens_in = 0
        tokens_out = 0
        for msg in messages:
            if msg.role == "user":
                human_turns += 1
            elif msg.role == "assistant":
                assistant_replies += 1
                tool_calls += len(msg.tool_names())
                model = msg.model or model
                if msg.usage is not None:
                    tokens_in += (
                        (msg.usage.input or 0)
                        + (msg.usage.cache_creation or 0)
                        + (msg.usage.cache_read or 0)
                    )
                    tokens_out += msg.usage.output or 0
        return AgentActivity(
            state=cls._fleet_tail_state(messages[-1]),
            title=title,
            current_task=current_task,
            human_turns=human_turns,
            assistant_replies=assistant_replies,
            tool_calls=tool_calls,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            last_event_at=messages[-1].timestamp,
            started_at=messages[0].timestamp,
        )

    @staticmethod
    def _fleet_tail_state(tail: AgentMessage) -> AgentActivityState:
        """One sub-agent's status from its tail message's block SHAPE — the
        ``AgentMessage`` analogue of ``_TranscriptParser._tail_state``. A fresh
        task or a just-returned tool result is the worker's move (WORKING); an
        assistant reply ending in an unresolved question tool is BLOCKED (rare
        for a sub-agent, but a real transcript-visible signal, same rule as the
        main thread); ending in any other ``tool_use`` is WORKING (mid tool
        loop); plain trailing text is WAITING (the worker's own turn closed)."""
        if tail.role != "assistant":
            return AgentActivityState.WORKING
        tool_calls = [b for b in tail.content if b.type == "tool_use"]
        if not tool_calls:
            return AgentActivityState.WAITING
        if any(AgentQuestion.recognizes(b.tool_name) for b in tool_calls if b.tool_name):
            return AgentActivityState.BLOCKED
        return AgentActivityState.WORKING

    @staticmethod
    def _fallback_description(messages: Sequence[AgentMessage]) -> str | None:
        """The truncated initial task prompt — used only when the ``.meta.json``
        sidecar is missing/malformed and carries no ``description``."""
        for msg in messages:
            if msg.role == "user":
                text = msg.text()
                return _truncate(text, _TASK_TEXT_CAP) if text.strip() else None
        return None

    def subagent_turns(
        self, cwd: Path, session_id: str, thread_id: str, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        """One sub-agent thread's own conversation, oldest first — the turns
        sibling of :meth:`fleet_activity`.

        A fleet row's ``session_id`` (used e.g. as the webapp's fleet-child
        transcript route param) IS the Claude sub-agent thread id
        (``agentId``) — never a top-level session id, since ``discover_paths``
        deliberately skips ``subagents/``. So no listing anywhere ever carries
        it, and the normal ``read_turns(cwd, thread_id)`` lookup always misses.
        This reads the ALREADY-normalized spine (:meth:`read_messages`, no
        second parser), filters to the one thread, and projects it through the
        SAME turn-builder the main thread uses
        (:meth:`_TranscriptParser._turns_from_messages`) so a fleet child's
        turns render with identical rules (question/file-edit/todo
        structuring). Mirrors :meth:`fleet_activity`'s cheap no-``subagents/``
        early exit; an unknown ``thread_id`` degrades to ``()``, never raises.
        """
        subagent_paths = {
            p.stem: p for p in self.locate_transcripts(cwd, session_id) if "subagents" in p.parts
        }
        if not subagent_paths:
            return ()
        thread_messages = [
            msg
            for msg in self.read_messages(cwd, session_id)
            if msg.is_sidechain and msg.thread_id == thread_id
        ]
        if not thread_messages:
            return ()
        return _TranscriptParser._turns_from_messages(
            thread_messages, last=last, include_sidechain=True
        )

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("activity", str(cwd), session_id),
            paths,
            lambda: _TranscriptParser(self._read(paths)).activity(),
        )

    def transcript_digest(self, cwd: Path, session_id: str) -> OrderedDigest:
        paths = self.locate_transcripts(cwd, session_id)
        return _MEMO.get_or_compute(
            ("digest", str(cwd), session_id),
            paths,
            lambda: _TranscriptParser(self._read(paths)).digest(),
        )

    @staticmethod
    def clear_caches() -> None:
        """Drop the incremental transcript cache + derived-result memo.

        A test seam (module-level caches outlive per-test tmp dirs) and an
        operational escape hatch; never needed on the hot path."""
        _TRANSCRIPTS.clear()
        _MEMO.clear()

    # ── answer driver ─────────────────────────────────────────────────────────
    @staticmethod
    def build_answer_keys(
        questions: Sequence[AgentQuestion],
        answers: Sequence[AnswerSelection],
    ) -> list[SendOp]:
        """Translate a validated answer plan into ``AskUserQuestion`` keystrokes.

        Pure and deterministic — the verified TUI grammar (on-host, Claude Code
        2.1.x) encoded exactly once. Raises ``ValueError`` for any plan that
        doesn't fit these questions (the manager maps that to a 422); it never
        invents keystrokes for an unverified UI (the provider-boundary rule).

        Grammar, per question in captured order:

        * single-select, predefined option ``i`` chosen → the digit ``i+1`` (the
          TUI selects it and auto-advances);
        * single-select, free-text answer → the digit ``len(options)+1`` (the
          synthetic "Type something." option), the text typed verbatim, Enter;
        * multiSelect → one digit per chosen option (each toggles), then Tab.

        A trailing Enter is appended IFF a review ("Submit answers") step exists
        — more than one question OR any multiSelect. A lone single-select question
        submits on its own digit, so it gets no trailing Enter.

        v1 answers only the option-bearing kinds (``single_select`` /
        ``multi_select``). A free-text answer is single-select only (the
        multiSelect toggle-vs-edit interaction is unverified); ``confirm`` and
        optionless ``free_text`` questions are rejected — their keystrokes were
        never verified.
        """
        if len(answers) != len(questions):
            raise ValueError(f"expected {len(questions)} answer(s), got {len(answers)}")
        ops: list[SendOp] = []
        for q, a in zip(questions, answers, strict=True):
            ops.extend(ClaudeCodeAdapter._answer_ops(q, a))
        # The review tab ("1. Submit answers" preselected) exists for any batch
        # with more than one question or any multiSelect; a lone single-select
        # already submitted on its digit.
        if len(questions) > 1 or any(q.multiselect for q in questions):
            ops.append(SendKey.ENTER)
        return ops

    @staticmethod
    def _answer_ops(q: AgentQuestion, a: AnswerSelection) -> list[SendOp]:
        """The keystrokes for one (question, answer) pair — see build_answer_keys."""
        if a.text is not None:
            if q.kind != "single_select":
                raise ValueError(
                    f"free-text answer is supported only on single-select questions, not {q.kind!r}"
                )
            # The synthetic "Type something." option sits at position len+1.
            return [str(len(q.options) + 1), a.text, SendKey.ENTER]
        if q.kind == "single_select":
            if len(a.indexes) != 1:
                raise ValueError("a single-select question takes exactly one option index")
            return [_digit_for(a.indexes[0], q)]
        if q.kind == "multi_select":
            if not a.indexes:
                raise ValueError("a multiSelect question needs at least one option index")
            return [*(_digit_for(i, q) for i in a.indexes), SendKey.TAB]
        raise ValueError(f"question kind {q.kind!r} cannot be answered by keystroke")

    # ── internal ──────────────────────────────────────────────────────────
    def _summarize(self, session_id: str, path: Path, mtime: float) -> SessionSummary:
        """One session's listing row, from a single parse of its main transcript."""
        return _MEMO.get_or_compute(
            ("summary", session_id, str(path)),
            [path],
            lambda: self._summarize_uncached(session_id, path, mtime),
        )

    def _summarize_uncached(self, session_id: str, path: Path, mtime: float) -> SessionSummary:
        records = self._read([path])
        parser = _TranscriptParser(records)
        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = 0
        activity = parser.activity()
        return SessionSummary(
            session_id=session_id,
            adapter_kind=self.kind,
            transcript_path=path,
            cwd=parser.recorded_cwd(),
            created_at=parser.created_at(),
            modified_at=(datetime.fromtimestamp(mtime, tz=UTC) if mtime > 0 else None),
            size_bytes=size_bytes,
            git_branch=parser.git_branch(),
            title=activity.title,
            first_prompt=parser.first_human_text(),
            last_prompt=parser.last_prompt_text(),
            activity=activity,
        )

    @staticmethod
    def _read(paths: Sequence[Path]) -> list[_Record]:
        """De-duped, merged, time-sorted records across the given files.

        Reading is incremental: :class:`TranscriptCache` folds each line into a
        per path-set :class:`_RecordFolder` exactly once, so a poll tick pays
        ``json.loads`` only for bytes appended since the previous read (the
        daemon-CPU fix, 2026-07-11) — a full-history re-parse pegged the
        executor threads on hosts with multi-GB transcript trees. The fold
        preserves the two identity layers documented on :class:`_RecordFolder`;
        the sort stays per call (appends keep it nearly sorted, so timsort is
        cheap). One behavioral nuance vs the old full re-read: a record's
        tiebreak ``index`` reflects *fold* order, so a no-timestamp record
        appended after another file was first read sorts after that file's
        records — real no-timestamp records live only in the file preamble,
        parsed first either way.
        """
        records = _TRANSCRIPTS.read(paths)
        records.sort(key=_sort_key)
        return records


def _sort_key(rec: _Record) -> tuple[float, int]:
    ts = rec.timestamp
    # Records without a timestamp keep their parse position via the index, sorting
    # stably rather than jumping to the epoch.
    return (ts.timestamp() if ts is not None else 0.0, rec.index)


class _RecordFolder:
    """The per path-set fold state behind :meth:`ClaudeCodeAdapter._read`.

    Applies the two identity layers to each line exactly once, because one
    JSONL line is NOT one logical record:

    - ``uuid`` is line identity. A repeated uuid is a resume/fork replaying
      history in another file → dropped.
    - ``dedup_key`` is logical-record identity. A new line under a seen key is
      a split-block sibling of the same assistant response (Claude Code 2.x
      writes one line per content block) → its blocks fold into the kept
      record via :meth:`_Record.absorb_continuation`. The old first-line-wins
      drop here lost every post-``thinking`` text and tool_use block — the
      "transcripts show no follow-ups" bug.

    ``absorb_continuation`` mutates the kept record's raw dict, which is safe
    here by construction: every raw dict is parsed privately for this fold
    state (see :mod:`transcript_cache`), and a sibling line is absorbed at most
    once because each line is folded at most once.
    """

    __slots__ = ("_by_key", "_index", "_seen_lines", "_unique")

    def __init__(self) -> None:
        self._seen_lines: set[str] = set()
        self._by_key: dict[str, _Record] = {}
        self._unique: list[_Record] = []
        self._index = 0

    def add(self, raw: dict[str, Any]) -> None:
        rec = _Record(raw=raw, index=self._index)
        self._index += 1
        uid = rec.uuid
        if uid is not None:
            if uid in self._seen_lines:
                return
            self._seen_lines.add(uid)
        kept = self._by_key.get(rec.dedup_key)
        if kept is not None:
            kept.absorb_continuation(rec)
            return
        self._by_key[rec.dedup_key] = rec
        self._unique.append(rec)

    def records(self) -> list[_Record]:
        return self._unique


_TRANSCRIPTS = TranscriptCache(_RecordFolder)
_MEMO = ResultMemo()
