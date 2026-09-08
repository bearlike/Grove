"""Pydantic config models, on-disk cascade loader, and schema export.

The model is the contract: every layer (defaults → user → project → project-local
→ env → CLI) deep-merges into a dict, then Pydantic validates once at the
single boundary. Unknown fields raise — caught typos beat silent acceptance.

`${repo}` / `${repo_name}` are stored verbatim in saved configs and expanded
only when consumed (inside `WorkspaceManager.create()`), so the same global
config can serve every repo without re-validation per invocation.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, ClassVar, Literal
from urllib.parse import urlparse

from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo

from grove.core import paths
from grove.core.admission import AdmissionLimits
from grove.core.errors import ConfigError, EnvSourceError

# `use_attribute_docstrings` is what PUBLISHES the prose under each field. Every
# field below carries a docstring written as behaviour, and `schema_to_md.py`
# prints a field's `description` verbatim onto the public reference page — but
# Pydantic exports an attribute docstring only when this is on, so without it the
# generated table renders a Description column that is empty for every field. The
# prose was always there; nothing warned that none of it was reaching the page.
# Consequence worth stating: these docstrings are PUBLISHED COPY, so they are
# covered by the same no-bare-`#<n>` guard the class docstrings already have.
_FROZEN = ConfigDict(
    extra="forbid", validate_default=True, frozen=True, use_attribute_docstrings=True
)
_MUTABLE = ConfigDict(extra="forbid", validate_default=True, use_attribute_docstrings=True)


# ─── nested submodels ───────────────────────────────────────────────────────


class WorktreeConfig(BaseModel):
    """Where worktrees live and how branches are named."""

    model_config = _FROZEN

    root_template: str = "${repo}/.worktrees"
    """Where worktrees live. Supports `${repo}`, `${repo_name}` and `~`."""

    branch_prefix: str = "grove/"
    """Prefix on every auto created branch."""


BranchMode = Literal["auto", "new", "existing", "remote", "root"]
"""Which branch-source variant the create form opens on."""

MODEL_ID_MAX_LENGTH = 64
MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:\[\]-]*\Z")


def validate_model_id(value: str | None) -> str | None:
    """Normalize a model id Grove will forward, or raise ``ValueError``.

    ONE rule with two callers — ``WorkspaceDefaults.model`` here and
    ``CreateWorkspaceRequest.model`` on the wire — because both end up as an
    argv token in ``claude --model <id>``. A saved default used to carry only a
    length cap while the wire carried this pattern, and the gap is not cosmetic:
    the moment the engine started resolving the saved default (below), a stored
    id beginning with ``-`` would have become a FLAG to the agent binary, which
    is the value-becomes-syntax class the branch-name guard already covers.

    This limits argv shape, not model semantics: providers still validate any id
    we forward. A separator is admitted unless dangerous, not excluded unless
    proven necessary — brackets occur in real gateway ids
    (``anthropic-opus-5[1m]``), and list-form argv with ``shell=False`` never
    expands them.
    """
    if value is None:
        return None
    model = value.strip()
    if not model:
        return None
    if len(model) > MODEL_ID_MAX_LENGTH:
        raise ValueError(f"model id {model!r} exceeds {MODEL_ID_MAX_LENGTH} characters")
    if not MODEL_ID_PATTERN.fullmatch(model):
        raise ValueError(f"invalid model id {model!r}")
    return model


class WorkspaceDefaults(BaseModel):
    """Your saved answers for a create that does not name one. Every field is optional and
    an unset one falls through to its normal source.

    Every field is optional, and ``None`` means "no saved default" — the field
    falls through to whatever the existing per-field cascade already resolved
    (``container.enabled`` for runtime, ``brief.enabled`` for brief, the agent's
    own default for model). Grove applies these in the engine, so every surface
    that creates a workspace — the CLI, the TUI, the web composer, MCP and
    issue-ops — honours them identically, and a create form showing you a
    pre-filled answer is showing you what will actually happen.

    Title is deliberately absent: it names one task, never a default. So are
    concrete branch and remote names, for the same reason.
    """

    # Why the engine resolves this rather than each client: it did not, once,
    # and the difference was invisible. `WorkspaceDefaultsView` honoured
    # `runtime` while `RuntimeResolver` read `container.enabled` — which is True
    # by default — so a saved `host` was displayed by every form and ignored by
    # every create whose client did not repeat the resolution itself.
    # `GroveConfig.default_runtime` / `default_brief` / `default_skip_init` are
    # that one resolution; `create` and the view both read them.
    model_config = _FROZEN

    agent: str | None = None
    """Agent selected when a create form opens."""
    # Runtime would cycle via workspace.py; keep its values literal here.
    runtime: Literal["host", "container"] | None = None
    """`host` or `container` for a new workspace. Unset falls through to
    `container.enabled`.
    """
    brief: bool | None = None
    """Whether a new agent receives Grove's first turn brief. Unset falls through to
    `brief.enabled`.
    """
    model: str | None = None
    """Model id sent to the selected agent. Unset uses the agent's own default."""
    branch_mode: BranchMode | None = None
    """How a create picks its branch. `auto`, `new`, `existing`, `remote` or `root`."""
    base_ref: str | None = None
    """The branch or ref a new branch starts from."""
    skip_init: bool | None = None
    """Whether to skip the configured init script."""

    @field_validator("model")
    @classmethod
    def _valid_model(cls, value: str | None) -> str | None:
        return validate_model_id(value)


class AgentCwdsConfig(BaseModel):
    """Named directories inside the repo an agent may start in. Only the agent session
    moves, the worktree, branch and init script stay at the root.

    A repository is often several projects, and in a large monorepo each
    directory belongs to a different team. This is the labelled set a create
    surface offers so nobody types a path, plus which one applies when a create
    names none.

    Only the AGENT SESSION's working directory moves. The git worktree, the
    branch and the init script stay anchored at the repository root, which is
    what lets several directories of one repo be distinct projects sharing one
    worktree family.

    Every path is relative and stays relative, so the set is portable: it
    describes the repository's own layout rather than one machine's disks, and
    a committed project config carries it to everyone who clones. Absolute
    paths and anything climbing out of the repo are refused here, at the point
    of definition, rather than at the create that would have used them.
    """

    model_config = _FROZEN

    entries: dict[str, str] = Field(default_factory=dict)
    """Label to repo relative path. The label is what a person picks, the path is what the
    agent starts in, and insertion order is picker order.
    """

    default: str | None = None
    """The label a create resolves to when it names no directory. Unset starts the agent at
    the worktree root.
    """

    @field_validator("entries")
    @classmethod
    def _relative_and_contained(cls, value: dict[str, str]) -> dict[str, str]:
        for label, raw in value.items():
            if not label.strip():
                raise ValueError("a working-directory label may not be blank")
            path = PurePosixPath(raw)
            if path.is_absolute() or raw.startswith("~"):
                raise ValueError(
                    f"working directory {label!r} must be relative to the repo root, got {raw!r}"
                )
            if ".." in path.parts:
                raise ValueError(f"working directory {label!r} may not climb out of the repo")
        return value

    @model_validator(mode="after")
    def _default_names_an_entry(self) -> AgentCwdsConfig:
        if self.default is not None and self.default not in self.entries:
            known = ", ".join(sorted(self.entries)) or "none declared"
            raise ValueError(f"default working directory {self.default!r} is not one of: {known}")
        return self

    def default_path(self) -> str | None:
        """The resolved relative path for :attr:`default`, or ``None``.

        The engine's single consumer. Returning the PATH rather than the label
        is what keeps the label a presentation concern: nothing downstream of
        here ever learns that labels exist.
        """
        return None if self.default is None else self.entries[self.default]


class DefaultsScope(StrEnum):
    """Which config layer a "save as defaults" write lands in."""

    USER = "user"
    PROJECT = "project"
    PROJECT_LOCAL = "project-local"

    def path(self, repo_root: Path | None) -> Path:
        """Absolute file this scope writes to.

        A project-targeted write without its repository would otherwise have no
        honest destination: silently falling back to the user file would make a
        team-default action change every project on this machine instead.
        """
        if self is DefaultsScope.USER:
            return paths.user_config_path()
        if repo_root is None:
            raise ConfigError(f"{self.value} defaults require a repository root")
        if self is DefaultsScope.PROJECT:
            return paths.project_config_path(repo_root)
        return paths.project_local_config_path(repo_root)


# Which AgentAdapter introspects an agent. Module-level alias so the persisted
# WorkspaceState.agent_kind (workspace.py) shares one source of truth with the
# config-side AgentSpec.kind — workspace already imports from config, so this
# direction has no cycle.
AgentKind = Literal["claude_code", "codex", "generic", "mewbo"]


class AgentSpec(BaseModel):
    """One selectable agent in the create picker. Anything terminal based works."""

    model_config = _FROZEN

    name: str
    """Picker identifier, and the merge key across cascade layers."""

    command: str
    """Shell command sent to the agent window. Quoted arguments and `$VAR` expansion work.
    """

    kind: AgentKind = "generic"
    """Which adapter reads this agent's session. `claude_code` and `codex` read transcripts
    for live state and tokens, `mewbo` reads a remote session over REST, `generic`
    tracks nothing.
    """

    env: dict[str, str] = Field(default_factory=dict)
    """Extra environment variables exported into the agent's window before launch."""

    models: tuple[str, ...] = ()
    """Model ids offered in the create form picker. A convenience list, never a validated
    allowlist. Empty falls through to the adapter's live discovery, which is capped at
    ten.
    """

    env_unset: tuple[str, ...] = ()
    """Variable names cleared before `env` is applied, so an ambient value cannot leak into
    the agent's window.

    The hermetic half of the launch env: a tmux pane inherits the
    tmux server's environment, which inherited the daemon's, so an ambient value
    (a profile selector like ``CLAUDE_CONFIG_DIR``) silently leaks daemon → server
    → pane → agent. Listing a var here ``unset``s it at the pane boundary, so the
    agent starts from a known base and ``env`` — or, when ``env`` is silent, the
    tool's own default — decides instead of whatever the daemon happened to carry.
    Unset runs first, so a key present in both ``env_unset`` and ``env`` ends up
    exported. Pure mechanism, not policy: the launcher just clears whatever vars
    the config names — no var name is hard-coded anywhere — and a future container
    launcher applies the same ``env`` / ``env_unset`` set at create time.
    """

    description: str = ""
    """One line label shown in the picker."""

    tools_offline: bool = False
    """Launch with network facing tools disallowed. Claude Code drops `WebFetch` and
    `WebSearch`, Codex turns off sandbox networking. No effect on `generic` or `mewbo`.
    """

    native: bool = True
    """Run the agent as a Grove owned native session instead of its interactive terminal.
    On by default for Claude Code and Codex, ignored by other kinds, and every create
    surface can override it per workspace.

    On by default for Claude Code and Codex: Grove launches ``claude -p`` on the
    stream-json protocol or ``codex app-server`` on stdio, holds the session's
    control channel (interrupt, model switch, peer mail, live facts) and prints
    the agent's output in the pane. Native permissions still apply. Set it to
    ``false`` for the interactive terminal UI instead — the built-in
    ``claude-terminal`` and ``codex-terminal`` entries are exactly that. Ignored
    for ``generic`` and ``mewbo`` agents, which have no native protocol to own.
    A native session cannot pause or resume; stop or recreate it instead.
    """

    NATIVE_KINDS: ClassVar[frozenset[str]] = frozenset({"claude_code", "codex"})

    @property
    def owns_native_session(self) -> bool:
        """Whether a launch of this agent is a Grove-owned native session.

        The ONE predicate every launch-shaped decision reads: ``native`` says
        what the operator wants, the kind says whether a protocol exists to
        want it on. Reading the flag alone would make a ``generic`` shell try
        to speak stream-json.
        """
        return self.native_for(None)

    def native_for(self, choice: bool | None) -> bool:
        """The launch mode one create takes: the request's choice, else this entry's.

        ``choice`` is ``CreateWorkspaceRequest.native``; ``None`` means the
        caller left it to the roster. The kind gate applies to both roads, so a
        checkbox on a shell entry cannot ask for a protocol that does not exist.
        """
        wanted = self.native if choice is None else choice
        return wanted and self.kind in self.NATIVE_KINDS

    @model_validator(mode="before")
    @classmethod
    def _mailbox_alias(cls, data: Any) -> Any:
        """``mailbox`` was this field's opt-in name; accept it once, warning.

        The rename inverted the default, so an old ``mailbox: true`` is a
        no-op and an old ``mailbox: false`` — nobody wrote one — would silently
        mean "terminal". Map it rather than forbid it so a config that worked
        yesterday still loads, and say so once per load.
        """
        if isinstance(data, dict) and "mailbox" in data:
            data = dict(data)
            value = data.pop("mailbox")
            logger.warning(
                "agents[].mailbox is deprecated; use `native` (agent {!r})", data.get("name")
            )
            data.setdefault("native", value)
        return data


class InitScriptConfig(BaseModel):
    """A setup script run in its own tmux window before the agent starts."""

    model_config = _FROZEN

    EXCLUSIVE_FIELDS: ClassVar[tuple[str, ...]] = ("inline", "path")
    """The script has exactly one source: at most one of these may be set.

    The constraint is declared on the model that owns it, not inside the cascade
    machinery, because two guards read it and must not drift: `ExclusiveGroups`
    resolves the group *across* layers before the merge, and the validator below
    rejects a single layer that sets both.
    """

    enabled: bool = False
    """Run the init script when a workspace is created."""
    shell: Literal["bash", "sh", "zsh"] = "bash"
    """Shell the script runs under."""
    inline: str | None = None
    """Inline shell snippet. Mutually exclusive with `path`."""

    path: str | None = None
    """Repo relative path to a script file. Mutually exclusive with `inline`."""

    timeout_seconds: int = 300
    """Seconds before the script is killed and counted as failed."""
    fail_fast: bool = True
    """A non zero exit rolls back the worktree, session and branch. Off leaves the
    workspace in `error` for you to inspect.
    """

    run_on_resume: bool = False
    """Run the script again when a paused workspace resumes."""

    applies_to: Literal["all", "host", "container"] = "all"
    """Which runtimes the script is for. `all`, `host` or `container`.

    A host-side setup step is redundant (or actively wrong) when the
    devcontainer's own lifecycle hooks already do that work, and vice versa.
    Deliberately NOT named `runtime`: `workspace.Runtime` is `host|container`
    with no `all` member, and one name for two domains is exactly the confusion
    this codebase warns about. Default `"all"` is the historical behavior, so no
    existing config changes meaning.
    """

    def applies(self, *, is_container: bool) -> bool:
        """Does this script apply to a workspace on this runtime?

        Takes the bare fact, not a `Runtime`: `workspace.py` imports this
        module, so importing the enum back would close a cycle — and the plain
        bool keeps the predicate pure and callable from anywhere. Not-applicable
        is `InitStatus.SKIPPED` at the call sites, the same as `enabled: false`.
        """
        if self.applies_to == "all":
            return True
        return self.applies_to == ("container" if is_container else "host")

    @model_validator(mode="after")
    def _reject_two_script_sources(self) -> InitScriptConfig:
        """Fail load when one layer names both script sources.

        Runs on the MERGED config, where cross-layer collisions have already been
        resolved (`ExclusiveGroups`) — so anything still holding both was authored
        that way in a single layer, a real mistake worth reporting at load time
        instead of at the first `create` in that repo. `tmux.run_init_script`
        keeps its own check for models built without validation.
        """
        provided = [name for name in self.EXCLUSIVE_FIELDS if getattr(self, name) is not None]
        if len(provided) > 1:
            joined = " and ".join(f"'{name}'" for name in provided)
            raise ValueError(
                f"init_script: {joined} are mutually exclusive — set exactly one "
                "(an inline snippet or a path to a script file), not both"
            )
        return self


class TmuxConfig(BaseModel):
    """tmux session naming and refresh cadences."""

    model_config = _FROZEN

    session_prefix: str = "grove-"
    """Prefix on every Grove tmux session name."""
    init_window_name: str = "init"
    """Window the init script runs in."""
    agent_window_name: str = "agent"
    """Window the agent runs in."""
    shell_window_name: str = "shell"
    """Window that holds a plain shell."""
    history_limit: int = 50_000
    """Scrollback lines kept per pane."""

    detached_size: str = Field(default="200x50", pattern=r"^$|^[0-9]{1,4}x[0-9]{1,4}$")
    """Size, as `<columns>x<rows>`, a session is created at while nothing is attached.

    A session created detached has no client to take its dimensions from, so
    tmux falls back to its own 80x24 default — an aspect ratio no current
    terminal has. Everything that reads the session before a human attaches
    sees that shape: the agent lays out its first screen for it, and the web
    dashboard's terminal pane and every `peek` read it forever, because those
    consume `capture-pane` output and never attach a client at all.

    This is a starting size, not a pin. `window-size latest` still resizes the
    window to whichever terminal attaches, so a human's own terminal continues
    to win; the value only decides what the session looks like until then, and
    for the surfaces where nothing ever attaches. Set it to whatever your
    terminals actually are. Empty keeps tmux's own default.
    """

    peek_pane_refresh_seconds: float = 0.25
    """How often the peek rail refreshes the pane.

    Bounded subprocess work; keep low for snappier feel, raise on slow
    machines or when watching a large pane.
    """

    peek_stats_refresh_seconds: float = 3.0
    """How often the peek rail refreshes git counts and diff stats.

    These don't change at sub-second granularity; rerunning them on every
    pane tick would burn IO without any user-visible benefit.
    """

    peek_history_lines: int = Field(default=500, ge=1)
    """Scrollback lines the pane snapshot captures.

    The live viewport is only ~40 rows, so without scrollback a long agent
    session previews as just its current screen — earlier output is never
    read. This is the captured/over-the-wire bound, not a viewport: each
    client (TUI rail, dashboard tile, webapp `<pre>`) tails or scrolls within
    it. Generous by default; raise to scroll further back, lower on slow links.
    """

    activity_threshold_seconds: int = Field(default=30, ge=1)
    """Seconds of pane silence before a workspace flips from active to idle. Too low and a
    thinking agent flickers, too high and the badge lags.
    """

    steer_settle_ms: int = Field(default=200, ge=0)
    """Milliseconds between pasting steered text and sending Enter, so the agent's TUI
    treats Enter as a submit rather than part of the paste. `0` disables the wait.
    """


class HooksConfig(BaseModel):
    """Grove managed Claude Code status hooks, for exact push based state.

    When ``enabled``, Grove launches ``claude_code`` agents with
    ``--settings <grove-hooks-settings>`` so a lightweight hook pushes exact
    lifecycle status (``WORKING`` / ``WAITING`` / ``BLOCKED`` / ``IDLE``) into a
    per-session sidecar that the Activity Dashboard prefers over polled status —
    giving precise *blocked-on-a-permission-prompt* that polling can't see, plus
    an immediate daemon refresh over the native http hook instead of
    waiting out the poll tick. On by default now that the sidecar is the
    primary live signal rather than a dormant opt-in sidecar (still a
    mechanism knob, not policy); the user's own ``.claude/settings.json`` is
    never touched, so disabling is just flipping this back to ``false``.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Launch Claude Code with Grove's managed hooks for exact push based status."""

    daemon_url: str = ""
    """Base URL the hook posts each event to.

    Empty (the default) keeps the built-in loopback address, so the rendered
    settings stay byte-identical to a config that never mentions this. It is a
    knob because the address is only correct for an agent sharing the daemon's
    loopback: a runtime launched into its own network namespace — a container —
    reaches the daemon at a different host entirely, and an address a runtime
    cannot resolve is policy that has no business being fixed in code.
    """


class BriefConfig(BaseModel):
    """The one paragraph brief a new agent is handed on its first turn, pointing it at
    Grove's `working-in-grove` skill.

    The brief says where the agent is, that what it reports is published onto
    the workspace's attached tickets, and which skill carries the rules; the
    skill itself carries everything else, so the cost is a few lines once per
    session, once per agent.

    How it arrives depends on what the workspace can carry. A ``claude_code``
    agent on the host is handed it by Grove's status hook, on its first prompt,
    whether or not the workspace was given a task. An agent with no such hook —
    one running in a container — gets it prepended to the workspace's initial
    prompt instead, so it is briefed only when a prompt was given. A remote
    (mewbo) agent is never briefed: it runs on a backend, with no worktree to
    report from.

    This is the *default* for new workspaces. ``grove create --no-brief``
    overrides it per workspace, and the choice is recorded, so flipping this
    later never changes a workspace that already exists.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Whether a new agent receives the brief at all."""

    instructions: str = ""
    """Your own text, appended to the brief every agent in this repo receives.

    Grove's own brief says where the agent is and which skill carries the rules.
    This is where a team adds what only they know: the review conventions, the
    tracker etiquette, the one command that must be run before pushing. It rides
    the same first-turn delivery, so it reaches a containerized agent that can
    reach neither the daemon nor the ``grove`` command.

    It cascades like everything else, so a machine-wide default set once in the
    user config applies to every project, and a repository's own
    ``.grove/config.json`` refines it for the people working in that repository.

    Keep it short. It is spent on the first turn of every session of every
    workspace, and a long one costs more attention than it buys. Anything a
    reader could look up on demand belongs in the repository's own guidance
    files, which the agent is already standing in.
    """

    self_naming: bool = True
    """Ask an agent whose workspace has no description to write one, and to replace a
    generated title with a real one.

    A workspace created without a title is named after a short generated id, on
    the promise that it stays renameable — but nothing was renaming it, so a
    fleet ended up named after hashes that say nothing about what each agent is
    doing. The agent is the one participant that learns the answer, usually
    within its first few turns.

    The nudge is added only for a workspace whose description is empty, so one
    a person already described is never asked to re-describe itself.
    """


AgentShare = Literal["full", "projects", "isolated"]
"""How much of the host agent configuration a containerized agent shares.

Ordered by :data:`SHARE_RANK` — ``isolated`` (tightest) → ``full`` (loosest).
The knob is what makes the container a *blast-radius* boundary rather than a
credential boundary: ``full`` means the agent works in-container exactly as it
does on the host (native sign-in, skills, settings), which is the point of
containerizing an agent that runs with permission prompts off.
"""

SHARE_RANK: dict[AgentShare, int] = {"isolated": 0, "projects": 1, "full": 2}
"""Sharing order, tightest first. THE single definition — the committed-layer
tighten-only rule (:class:`CommittedShareFloor`) and the mount planner
(``core.container_policy``) must agree on what "tighter" means."""

EgressMode = Literal["allowlist", "open", "deny"]
"""Network posture for a containerized agent: derived allowlist (default), no
firewall at all, or nothing but the workspace's own network."""


def _default_agent_plane() -> dict[AgentKind, tuple[str, ...]]:
    """Per-kind agent-plane endpoints, keyed like ``ProxyConfig.upstreams``."""
    return {
        "claude_code": ("api.anthropic.com", "console.anthropic.com", "statsig.anthropic.com"),
        "codex": ("api.openai.com", "auth.openai.com", "chatgpt.com"),
    }


class RangeSource(BaseModel):
    """A provider that publishes its own address ranges as JSON.

    Mechanism, not a GitHub special case: any endpoint returning a JSON object
    whose named keys are arrays of CIDR strings is expressible here, so the next
    provider that publishes ranges costs a config entry rather than a branch.

    ``keys`` is explicit rather than "take everything" because a provider
    publishes ranges Grove has no business allowing — GitHub's payload also
    carries `actions`, `packages` and `codespaces`, which are not what a
    workspace's `git fetch` needs.
    """

    model_config = _FROZEN

    url: str
    """Where the JSON lives. Fetched from the host, never from inside the container."""

    keys: tuple[str, ...] = ()
    """Which top level keys hold the CIDR arrays. Empty takes every key whose value is an
    array of strings.
    """


def _default_range_sources() -> tuple[RangeSource, ...]:
    """GitHub, because it is where the measured damage is.

    `git` and `web` cover clone/fetch over HTTPS and the redirects that follow;
    `api` covers `gh`. Deliberately not `actions`/`packages`/`codespaces` — a
    workspace does not need GitHub's whole estate to fetch its own repository.
    """
    return (RangeSource(url="https://api.github.com/meta", keys=("git", "web", "api")),)


def _default_package_plane() -> tuple[str, ...]:
    """Package indexes and registries ordinary dev work reaches for."""
    return (
        "registry.npmjs.org",
        "pypi.org",
        "files.pythonhosted.org",
        "github.com",
        "codeload.github.com",
        "objects.githubusercontent.com",
        "raw.githubusercontent.com",
        "proxy.golang.org",
        "index.crates.io",
        "static.crates.io",
        "deb.debian.org",
        "archive.ubuntu.com",
        "registry-1.docker.io",
        "auth.docker.io",
        "ghcr.io",
    )


class ContainerAgentConfig(BaseModel):
    """What the container shares from your host agent configuration.

    Default ``full``: native integration with the host — config, sign-in,
    skills — is a *feature*, delivered through bind mounts, and it is what makes
    a fully-autonomous agent useful rather than a second sign-in chore.
    ``isolated`` restores the credential boundary for an untrusted repository at
    the cost of that sign-in; ``projects`` shares transcripts only.

    **Trust rule (enforced in :class:`CommittedShareFloor`): a committed layer
    may force a tighter value but never raise sharing.** A committed
    ``.grove/config.json`` is untrusted input — a repo that could set
    ``share: full`` would be granting itself the host's credentials.
    """

    model_config = _FROZEN

    share: AgentShare = "full"
    """How much of your agent configuration the container sees. `full` mounts sign in,
    skills and memory, `projects` mounts transcripts only, `isolated` gives the
    container its own config directory. A committed layer can only tighten it.
    """

    trust: bool = True
    """Seed the workspace folder as already trusted and its committed `.mcp.json` servers
    as already approved, so the agent never stops on a first run dialog nobody can
    answer.

    On by default because a container workspace is an *unattended* start: the
    agent runs in a directory the tool has never seen, and the tool asks — once,
    interactively — whether that directory can be trusted. Nobody is at the
    terminal to answer, so the workspace simply never begins working. Turning
    this off restores that prompt, which means a containerized agent will not
    start on its own until a human attaches and answers it; the isolation the
    container itself provides is unchanged either way.
    """


class ContainerTmuxConfig(BaseModel):
    """Whether the agent runs under a tmux inside its container, and whose tmux.

    The multiplexer used to sit on the far side of the namespace boundary from
    the process it multiplexes: the agent ran as ``devcontainer exec … -- claude``
    typed into a HOST pane, so the PTY died with the host client and nothing
    could ever attach back to the surviving in-container process. Running the
    agent under a container-side tmux makes the host pane a *viewport* and the
    in-container tmux the owner of the agent's lifetime.

    Two policy questions live here rather than in code, because both are
    genuinely the operator's:

    * ``prefer_image`` — an image that ships its own tmux is almost always the
      better answer (it matches the distro's terminfo and the user's own
      expectations), but an operator standardizing on one tmux across a fleet
      may want Grove's bundle everywhere.
    * ``payload`` — where the static binary comes from. Empty means Grove's own
      built-and-cached bundle; a path lets an operator supply a vetted build
      (an air-gapped host, a signed artifact, a different tmux version).

    Everything degrades honestly: with no image tmux and no payload, the launch
    composes a plain ``exec`` and the workspace loses persistence, never the
    workspace itself.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Run the agent under a tmux inside the container when one is reachable. Off is a bare
    exec with no persistence.

    ``False`` restores the bare ``devcontainer exec -- <agent>`` launch: the
    agent still runs, but its terminal dies with the host client and there is
    no reattach path.
    """

    prefer_image: bool = True
    """Use the image's own tmux when it has one, otherwise Grove's bundle.

    Presence is detected once per provision through the same
    ``devcontainer exec`` road the agent itself takes, so the probe sees the
    remote user's PATH rather than the image's default user's.
    """

    payload: str = ""
    """A host directory holding `bin/<arch>/tmux` and a `terminfo/` tree, in place of
    Grove's bundle.

    Empty (default) uses Grove's own cache, built on demand. A value is an
    operator-supplied bundle, mounted read-only exactly like the built one and
    never verified beyond "the file is there" — it is the operator's build, and
    Grove never writes into it.

    The per-architecture split is not optional even for a hand-built bundle: the
    binary executes INSIDE the container, so an amd64 build cannot run in an
    arm64 container, and one Grove host routinely serves both (an Apple Silicon
    machine running an amd64 image, or the reverse under emulation). ``<arch>``
    is docker's platform name — ``amd64`` or ``arm64``. Compiled terminfo is
    capability data rather than machine code, so one ``terminfo/`` serves every
    architecture.
    """

    session: str = "agent"
    """The in container tmux session the agent runs in. Renaming it orphans a live agent.

    This is a REATTACH IDENTITY, not a display name, which is why it is its own
    field rather than a reuse of ``tmux.agent_window_name``: renaming the host
    window is cosmetic, while renaming this orphans a live agent's session
    behind a newly-created empty one.
    """

    shell_session: str = "shell"
    """The in container tmux session `grove shell` attaches to.

    Its own field for the same reason :attr:`session` is one — a reattach
    identity, not a display name — and separate FROM it because the shell and
    the agent are two sessions on one in-container server: sharing a name would
    drop a user into the agent's own pane. ``grove shell`` and the attach
    layout's shell window both key on this, which is what makes them the same
    persistent shell rather than two.
    """

    term_fallback: str = "xterm-256color"
    """`TERM` retried when tmux refuses the client's own. Empty disables the retry.

    Empty DISABLES the retry — one field rather than a flag plus a value,
    because a bool and a string can express "enabled with no TERM", which means
    nothing.

    Needed structurally, not defensively: an attach with a ``TERM`` no terminfo
    database has is a hard refusal (``missing or unsuitable terminal``), and
    several real terminals (``wezterm``, ``contour``, ``wayst`` depending on the
    distro) ship entries no ncurses release packages, so no bundle can ever
    cover everyone. Verified through a real pty with a real curses app that a
    substituted ``TERM`` renders, resizes on SIGWINCH, detaches and reattaches
    correctly — tmux and ncurses cannot tell a substituted value from a native
    one. Grove SAYS so on stderr when it substitutes; a silent one would leave a
    user debugging their own terminal's colours.
    """


class ContainerDecorConfig(BaseModel):
    """Whether the container gets Grove's tmux chrome and statusline.

    Grove bind-mounts a small read-only asset bundle into every containerized
    workspace at ``/grove/decor``: a tmux config and a Claude Code statusline
    script. Both exist because a person attached to a container workspace
    otherwise has no way to tell they are in one — the user's own statusline
    script is never shared into the container, and the in-container tmux has
    no config at all — and because a status bar inside a container must
    report the CONTAINER's own cgroup CPU/memory limits, not
    ``/proc/loadavg``, which is not namespaced and answers for the HOST.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Mount and compose the terminal chrome bundle at all."""

    statusline: bool = True
    """Compose Grove's statusline into the agent's settings."""

    tmux_conf: bool = True
    """Pass Grove's tmux config to the in container tmux."""

    payload: str = ""
    """A host directory of your own decor assets, mounted read only in place of Grove's
    bundle.
    """


class EgressConfig(BaseModel):
    """Where a containerized agent may reach on the network.

    With credentials shared and permission prompts off, egress is the control
    that carries the weight: it bounds where a token can be *sent*. The list is
    **derived, not restated** — the planner (``core.container_policy``) unions
    the agent plane for the workspace's kind, the package plane, the repo's own
    git remotes, the Grove plane and the telemetry endpoint's host, so normal
    dev work needs zero config. ``allow`` is purely additive on top.

    The telemetry entry has no field of its own for the same reason the git
    remotes do not: the destination is already stated once, as the host the
    ``telemetry`` section resolves, and a second place to write it is a second
    place for it to be wrong. A self-hosted LAN endpoint is therefore covered
    by default — which matters because a firewalled OTLP export fails as
    silence, not as an error.

    Documented ceilings, carried from the reference implementation this follows:
    UDP/53 stays open (DNS tunneling is not defended against) and name-based
    filtering loses to domain fronting. ``open`` is the supported, un-nagged
    no-firewall path — never a warning, never a refusal.
    """

    model_config = _FROZEN

    mode: EgressMode = "allowlist"
    """`allowlist` applies the derived firewall, `open` applies nothing, `deny` permits
    only loopback and the workspace's own network. Anything but `open` fails closed if
    the firewall cannot be applied.
    """

    allow: tuple[str, ...] = ()
    """Extra hostnames or CIDRs added to the derived allowlist."""

    agent_plane: dict[AgentKind, tuple[str, ...]] = Field(default_factory=_default_agent_plane)
    """Per agent kind provider endpoints. A gateway deployment repoints them here."""

    package_plane: tuple[str, ...] = Field(default_factory=_default_package_plane)
    """Package indexes and registries reachable regardless of agent kind."""

    grove_plane: tuple[str, ...] = ("host.docker.internal",)
    """How the container reaches Grove itself for hook ingest and the daemon."""

    range_sources: tuple[RangeSource, ...] = Field(default_factory=_default_range_sources)
    """Providers that publish their address ranges as JSON, fetched from the host at
    provision time, for hosts whose DNS answers outlive their usefulness.

    Additive to the hostname planes above, never a replacement: a source that
    fails to fetch degrades to exactly today's behaviour rather than to nothing.
    Config rather than code so the next provider that publishes ranges needs an
    entry, not a branch.
    """


class ResourcesConfig(BaseModel):
    """Per container caps, applied at launch.

    One knob on the policy cascade, deliberately NOT a slice hierarchy: the
    agent container gets them via ``docker update`` after ``up`` (the
    devcontainer CLI owns creation, so post-hoc update is the one dependable
    application point) and compose stack services via ``deploy.resources`` in
    the generated override. Empty/zero means "do not cap" — every field is
    independently optional, so a memory-only policy emits a memory-only update.

    ``hostRequirements`` in a ``devcontainer.json`` is a different question — a
    *declaration* Grove checks and refuses on, never a cap it applies.
    """

    model_config = _FROZEN

    memory: str = ""
    """Memory cap such as `8g` or `512m`. Empty is uncapped."""

    cpus: str = ""
    """CPU cap such as `4` or `1.5`, as a string. Empty is uncapped."""

    pids: int = 0
    """Process count cap. `0` is uncapped."""


class EnvSourceConfig(BaseModel):
    """Where a section reads its secrets from. A file or a command, never a literal in
    config.

    The two knobs below are the ONE mechanism Grove has for "give this feature an
    environment it could not read from my own process". They exist because the
    process that consumes them is long-lived: a daemon reads ``os.environ`` once,
    at exec, and nothing a workspace's init script (or a secret manager, or a
    ``docker login``) does afterwards can reach back into it. A file or a command
    is re-read at the moment of use, so a credential that only exists AFTER the
    daemon started is still found.

    Subclassed rather than duplicated — ``container`` and ``tickets`` are two
    consumers of one seam, and every guard around it (``ExclusiveGroups``,
    ``CommittedEnvSource``, ``core.env_source.EnvSource``) reads this one
    definition. A third consumer costs a ``SECTION`` and a base class, never a
    second parser.

    **Never a field holding the secret itself.** A value belongs in the file or
    the command's stdout; config holds only names and paths, which is what keeps
    a committed ``.grove/config.json`` publishable.
    """

    model_config = _FROZEN

    SECTION: ClassVar[str]
    """The config key this section is mounted at (``container``, ``tickets``).

    Declared here so the cross-layer guards and the resolver can name the section
    in their own messages without a caller passing it in — the same reasoning that
    puts ``EXCLUSIVE_FIELDS`` on the model that owns the fields.
    """

    EXCLUSIVE_FIELDS: ClassVar[tuple[str, ...]] = ("env_file", "env_command")
    """This section's environment has exactly one source: at most one of these.

    Declared on the model that owns the fields, not inside the cascade machinery,
    for the same reason as `InitScriptConfig.EXCLUSIVE_FIELDS`: two guards read it
    and must not drift — `ExclusiveGroups` resolves the group *across* layers
    before the merge, and the validator below rejects a single layer setting both.
    """

    env_file: str | None = None
    """Dotenv file this section's variables are read from. Repo relative or absolute, `~`
    expanded. A configured file that is missing is an error at use.

    Repo-relative or absolute, ``~`` expanded. Mutually exclusive with
    ``env_command``. A configured-but-missing file is a HARD ERROR at the point of
    use — a silently-empty environment is the exact failure this feature exists to
    fix, and a workspace that boots without its credentials burns a whole session
    discovering that. The error is raised at the consumption boundary
    (``core.env_source``), not here, so the cascade can still persist a config for
    a repo whose file is not present yet.

    **A committed layer may only name a path INSIDE the repo** — see
    :class:`CommittedEnvSource`.
    """

    env_command: str | None = None
    """Host command whose stdout is parsed as dotenv, so a secret never touches disk. Re
    run at every use and never honored from a committed layer.

    Exists so secrets never have to be materialized to disk: any command that
    prints dotenv to stdout works, and Grove knows nothing about any particular
    secret manager — naming one here would be policy in code. Mutually exclusive
    with ``env_file``. Re-run at every consumption (deliberately not cached — a
    cache in a daemon holds secrets for days and serves rotated ones), so it must
    be idempotent and cheap.

    **Never honored from a committed layer** — running it would be remote code
    execution on clone. See :class:`CommittedEnvSource`.
    """

    @staticmethod
    def env_file_is_contained(raw: str) -> bool:
        """Does this ``env_file`` value stay inside the repository?

        The predicate lives on the model that owns the field so the committed-layer
        pre-pass and any future guard read ONE definition — the same reasoning that
        puts `EXCLUSIVE_FIELDS` here. Purely string-level on purpose: the cascade
        resolves long before a repo root or a filesystem is in hand, and a check
        that needed either could not run where it is needed.

        Rejects ``~`` (a home path is not repo-relative however it normalizes), any
        absolute form including the Windows drive/UNC shapes `PurePosixPath` does
        not consider absolute, and any path whose normalized form walks above the
        root.
        """
        candidate = raw.replace("\\", "/")
        if candidate.startswith("~"):
            return False
        if PurePosixPath(candidate).is_absolute():
            return False
        # "C:/secrets.env" is relative to PurePosixPath but absolute where it runs.
        if len(candidate) > 1 and candidate[1] == ":" and candidate[0].isalpha():
            return False
        normalized = os.path.normpath(candidate).replace("\\", "/")
        if normalized.startswith("/"):
            return False
        # Segment compare, not a prefix test: "..secrets.env" is an ordinary name.
        return normalized.split("/", 1)[0] != ".."

    @model_validator(mode="after")
    def _reject_two_env_sources(self) -> EnvSourceConfig:
        """Fail load when one layer names both environment sources.

        Runs on the MERGED config, where cross-layer collisions were already
        resolved (`ExclusiveGroups`), so anything still holding both was authored
        that way in a single layer — a real mistake, and cheaper to report at load
        than at the first use in that repo.
        """
        provided = [name for name in self.EXCLUSIVE_FIELDS if getattr(self, name) is not None]
        if len(provided) > 1:
            joined = " and ".join(f"'{name}'" for name in provided)
            raise ValueError(
                f"{self.SECTION}: {joined} are mutually exclusive — set exactly one "
                "(a dotenv file or a command printing dotenv to stdout), not both"
            )
        return self


class ContainerConfig(EnvSourceConfig):
    """Run the agent inside a container built from the repo's own devcontainer.

    **Default-ON, deliberately reversing Grove's earlier "containers are
    strictly opt-in" default.** The container is what lets an agent run fully
    autonomously (relaxed permissions) with its blast radius bounded to the
    workspace, so it is the default new workspaces get; ``--runtime host`` is
    the per-create escape hatch and the persisted ``WorkspaceState.runtime``
    means an existing workspace never moves. Turning it back off is a policy
    choice about isolation, not a bug fix.

    Every value cascades like the rest of the config; a future Podman driver
    reads the SAME submodel behind the same protocol — ``docker_bin`` names the
    CLI, it is not the driver-swap seam.

    The inherited ``env_file`` / ``env_command`` knobs (:class:`EnvSourceConfig`)
    are resolved TWICE per workspace start: once to provision (the values reach
    the project's lifecycle hooks via ``--secrets-file``) and once to launch (they
    reach the agent via the launch env). A missing file or a failing command is
    fatal at create, which is what keeps an agent from booting without its
    credentials.
    """

    model_config = _FROZEN

    SECTION: ClassVar[str] = "container"

    enabled: bool = True
    """The default runtime for a new workspace. `true` is container, `false` is host. Read
    only at create, so flipping it never moves an existing workspace.
    """

    up_timeout_seconds: float = 900.0
    """Seconds one `devcontainer up` or build may take before the create counts as failed.
    A cold build is legitimately minutes long.
    """

    default_config: str = ""
    """The `devcontainer.json` used for a repo that has none of its own. Empty uses the
    self contained config packaged with Grove.
    """

    docker_bin: str = "docker"
    """The docker compatible CLI Grove shells out to. A name on `PATH` or an absolute path.
    """

    shell: tuple[str, ...] = ("bash", "sh")
    """Shells `grove shell` tries inside the container, in order.

    A CHAIN rather than one name, because the shell is a property of somebody
    else's image and Grove cannot know it: ``bash`` is absent from Alpine-based
    and distroless-ish images that nonetheless ship ``sh``, while a user whose
    image ships ``zsh`` or ``fish`` wants that one. The first entry that exists
    inside the container is exec'd; if none does, the pane says so and exits
    non-zero rather than silently substituting a shell nobody asked for — a
    baked-in final fallback would be exactly the policy-in-code this config
    exists to avoid.

    Resolution happens INSIDE the container (``command -v``), not here: presence
    is a fact about the image, and probing it from the host would be a second
    ``exec`` answering a question the shell itself answers for free.
    """

    agent_config: ContainerAgentConfig = Field(default_factory=ContainerAgentConfig)
    """What the container shares from the host agent configuration."""

    egress: EgressConfig = Field(default_factory=EgressConfig)
    """Where a containerized agent may reach on the network."""

    tmux: ContainerTmuxConfig = Field(default_factory=ContainerTmuxConfig)
    """Whether the agent runs under a tmux inside its container, and whose tmux."""

    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    """Per container caps, applied at launch."""

    decor: ContainerDecorConfig = Field(default_factory=ContainerDecorConfig)
    """Whether the container gets Grove's own tmux chrome and statusline."""


class ChannelsConfig(BaseModel):
    """Grove managed Claude Code channel delivery, a research preview.

    A channel is Claude Code's native seam for pushing a message a **running,
    interactive** session acts on (and relaying permission decisions), unlike a
    hook (status push, one way) or steering (raw pane keystrokes). When
    ``enabled``, Grove launches ``claude_code`` agents with ``--channels
    <grove-channel-settings>`` so the agent connects to the Grove channel MCP
    server; the daemon then POSTs queued messages to that server's loopback
    receiver and the agent receives them as ``notifications/claude/channel``.

    Off by default on purpose: channels are an auth-gated Claude Code research
    preview, so this stays a deliberately-flipped mechanism knob (not policy).
    Disabling is just flipping ``enabled`` back — the launch flag disappears and
    the whole path degrades to a no-op, exactly like the hook ``--settings``.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Whether Grove delivers channel messages into running Claude Code sessions."""

    admission: AdmissionLimits = Field(default_factory=AdmissionLimits)
    """Item and byte reservations held until each accepted delivery has an outcome."""

    allowed_senders: list[str] = Field(default_factory=list)
    """Sender labels allowed to post into a running session's channel. Empty allows every
    sender, so populate it to restrict.
    """


class PermissionConfig(BaseModel):
    """Grove hosted answering of Claude Code's permission prompts.

    A *permission prompt* is the "allow this tool call?" gate a headless / paneless
    session hits with no interactive terminal to answer it. When ``enabled``, Grove
    launches ``claude_code`` agents with a Grove-owned MCP server registered
    (``--mcp-config <grove-permission-mcp>``) plus
    ``--permission-prompt-tool mcp__grove_permission__permission_prompt`` — so
    Claude Code calls that Grove tool instead of blocking on a TTY, and the tool
    answers with allow/deny JSON. It is the native replacement for typing a
    permission answer into the tmux pane (which only works with a human attached).

    Off by default on purpose (mechanism, not policy), and **fail-closed**:
    ``default = "deny"`` means an un-relayed prompt is denied, never silently
    allowed — flipping ``enabled`` on can't widen what an unattended agent may do
    without an explicit ``default: "allow"``. Disabling is just flipping
    ``enabled`` back — the launch flags disappear and the whole path is a no-op,
    exactly like the hook ``--settings`` / channel ``--channels`` appends.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Let Grove answer Claude Code's permission prompts through its prompt tool."""

    default: Literal["allow", "deny"] = "deny"
    """The answer while no human relay is wired. `deny` fails closed for an unattended
    agent, `allow` is the deliberate opt in for a bounded sandbox.
    """


class TLSConfig(BaseModel):
    """Extra certificate authority roots for Grove's outbound TLS clients."""

    model_config = _FROZEN

    ca_path: str = Field(default="", json_schema_extra={"x-env-var": "GROVE_TLS_CA_PATH"})
    """A PEM bundle or OpenSSL hashed CA directory trusted alongside the operating system's
    roots.

    Use this when Grove must reach a private forge, gateway, or collector whose
    root CA cannot be installed in the operating system store. Empty (the
    default) uses only the operating system roots. A named path is checked when
    Grove starts; a missing or unreadable path stops startup instead of silently
    falling back to the default trust set.
    """


class AuthConfig(BaseModel):
    """Daemon authentication and pairing limits.

    The handshake-based pairing flow gates every HTTP entry point on a valid
    bearer token (no loopback bypass; see CLAUDE.md). ``enabled = false`` is
    a test-only escape hatch; production daemons leave it ``true``.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Master switch for daemon authentication. Only in process tests turn it off."""

    session_ttl_seconds: int = Field(default=30 * 24 * 3600, ge=60)
    """Sliding session lifetime. Every request extends it, so a daily user never re pairs
    and an idle device ages out.
    """

    pairing_ttl_seconds: int = Field(default=300, ge=30)
    """How long a pairing code stays valid for approval."""

    pair_init_per_minute: int = Field(default=5, ge=1)
    """Per source rate limit on starting a pairing, which bounds brute force."""

    pair_poll_per_minute: int = Field(default=60, ge=1)
    """Per source rate limit on polling a pairing. The browser polls every two seconds
    while it waits for approval.
    """


class MewboConfig(BaseModel):
    """Connection settings for `kind: "mewbo"` agents.

    The ``mewbo`` adapter reads these to reach the Mewbo API: a workspace of
    that kind mints its session on the orchestrator rather than in a local
    process, so the connection details are config rather than discovery.
    """

    model_config = _FROZEN

    base_url: str = "http://127.0.0.1:5125"
    """Base URL of the Mewbo REST API."""

    api_key_env: str = "MEWBO_API_KEY"
    """Name of the environment variable holding the API key, never the key itself."""

    timeout_seconds: float = 10.0
    """Per request HTTP timeout."""


class GiteaTicketConfig(BaseModel):
    """Gitea Issues provider settings.

    Secret-free like every config layer: ``token_env`` is the NAME of the
    environment variable holding the API token, never the token itself, so a
    committed ``.grove/config.json`` stays publishable. ``base_url`` is the
    instance root (the provider appends ``/api/v1``), and it also reads from
    ``GROVE_GITEA_BASE_URL``: ``token_env`` already lets an operator instrument
    the token, so leaving the endpoint config-only would mean a deployment could
    move the credential and not the server it authenticates against — which is
    how a token ends up pointed at the wrong host. ``branch_prefix`` is an
    optional extra keyword prepended when formatting a branch (e.g. ``"gtea-"``
    → ``gtea-123-slug``); empty means the bare numeric form ``123-slug``. The
    parser recognizes the bare numeric leading segment, the built-in keywords,
    and this configured prefix — mechanism, not policy.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Turn the provider on."""
    base_url: str = Field(
        default="https://gitea.com", json_schema_extra={"x-env-var": "GROVE_GITEA_BASE_URL"}
    )
    """Your Gitea instance."""
    owner: str | None = None
    """Repository owner."""
    repo: str | None = None
    """Repository name."""
    token_env: str = "GROVE_GITEA_TOKEN"
    """Name of the environment variable holding the API token, never the token."""
    branch_prefix: str = ""
    """A branch prefix that marks a bare number as a Gitea ticket."""


class GitHubTicketConfig(BaseModel):
    """GitHub Issues provider settings.

    ``base_url`` defaults to the public REST API; point it at a GitHub
    Enterprise ``/api/v3`` root to use Enterprise. Same secret-free
    ``token_env`` + optional ``branch_prefix`` contract as the Gitea provider.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Turn the provider on."""
    base_url: str = "https://api.github.com"
    """The GitHub API root. Change it for GitHub Enterprise."""
    owner: str | None = None
    """Repository owner."""
    repo: str | None = None
    """Repository name."""
    token_env: str = "GROVE_GITHUB_TOKEN"
    """Name of the environment variable holding the API token, never the token."""
    branch_prefix: str = ""
    """A branch prefix that marks a bare number as a GitHub ticket."""


class LinearTicketConfig(BaseModel):
    """Linear provider settings.

    Linear keys are alphanumeric (``ENG-123``), so there is no numeric
    ``branch_prefix`` — the team key IS the discriminator. ``team_key`` scopes
    both branch parsing (only ``{team_key}-N`` keys are claimed) and the
    ``get_ticket`` lookup; leave it unset to match any uppercase key on parse.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Turn the provider on."""
    base_url: str = "https://api.linear.app/graphql"
    """The Linear API root."""
    team_key: str | None = None
    """The team key that prefixes your issue ids, such as `ENG`."""
    token_env: str = "GROVE_LINEAR_TOKEN"
    """Name of the environment variable holding the API token, never the token."""


class TicketsConfig(EnvSourceConfig):
    """Ticket tracker integration, one block per provider.

    Each provider is independently ``enabled`` and configured. All three stay
    off by default (mechanism, not policy): a repo opts in by enabling the
    tracker its branches reference. Credentials never live here — only the NAME
    of the env var holding each token, and optionally where to READ that env var
    from (the inherited ``env_file`` / ``env_command``).

    **Resolution order for one provider's token, evaluated at the moment of use
    and never at construction:**

    1. ``tickets.env_command``'s stdout, or ``tickets.env_file``'s contents,
       parsed as dotenv and looked up by that provider's ``token_env`` name.
       The source is re-read on every resolution, so a credential an init script
       (or a secret manager, or a login) produces AFTER the daemon started is
       picked up without a restart.
    2. the consuming process's own environment (``os.environ``), same name.
    3. nothing — the provider reads ``configured: false`` and every network call
       raises rather than sending an unauthenticated request.

    One source serves all three providers because it is keyed by env-var NAME:
    a repo whose ``.grove/config.local.json`` sets
    ``"env_command": "my-secrets export grove"`` gets Gitea's, GitHub's and
    Linear's tokens from one resolution. Per-project configuration is the plain
    cascade (user → committed ``.grove/config.json`` → machine-local
    ``.grove/config.local.json``), which this section has always had.
    """

    model_config = _FROZEN

    SECTION: ClassVar[str] = "tickets"

    gitea: GiteaTicketConfig = Field(default_factory=GiteaTicketConfig)
    """Gitea Issues provider settings."""
    github: GitHubTicketConfig = Field(default_factory=GitHubTicketConfig)
    """GitHub Issues provider settings."""
    linear: LinearTicketConfig = Field(default_factory=LinearTicketConfig)
    """Linear provider settings."""


# The built-in initial prompt an issue-ops-created workspace boots on. Placeholders
# (``{title} {body} {number} {url} {command_text}``) are filled at the engine
# boundary via ``str.format_map`` with a missing-key-tolerant map, so a
# user-overridden template that references an unknown name renders it literally
# rather than crashing the router. Mechanism, not policy: every deployment can
# override the whole string via ``issueops.prompt_template`` and it cascades like
# any other config value — this is only the sensible default.
_DEFAULT_ISSUEOPS_PROMPT = """\
You are handling tracker issue #{number}: "{title}".

Issue description:
{body}

Thread so far:
{comments}

How you were engaged:
{command_text}

Your mandate:

- Work autonomously through to an OPEN pull request. Read the issue, map the
  affected components (read the nearest owning CLAUDE.md before editing),
  implement the change, run the project's gates, open the PR, and reply on the
  ticket saying what changed and where the PR is.
- Where the ticket is underspecified, REPLY ON THE TICKET asking for exactly
  what is missing, and say what you will assume if nobody answers. Do not stall
  silently, and do not quietly guess at a requirement you could have asked
  about.
- Satisfy the stated goals faithfully and safely — the goals as written, not
  the larger project you would rather do.
- Stop and ask rather than take a risky or irreversible action: destroying
  data, force-pushing a shared branch, or touching anything in production.

Issue link: {url}
"""


class IssueOpsConfig(BaseModel):
    """Turn issue comment mentions into workspace actions, and mirror progress back onto
    the ticket.

    One submodel, two faces. INBOUND: a commenter mentions the ``trigger``
    token as the first word of an issue comment; the forwarder (a stateless CI
    action) POSTs the event to the daemon, and the engine parses the grammar,
    enforces the permission policy, and routes to a workspace verb. OUTBOUND:
    when ``enabled``, the daemon runs a status publisher that mirrors each
    workspace's progress onto its ticket as one live sticky comment. Every knob
    here is mechanism, not policy — the trigger word, who may drive it, the boot
    prompt, and the mirror's cadence are all data the deployment owns, never baked
    into the engine.

    ``enabled`` gates ONLY the outbound status mirror. The inbound command routing
    has no on/off flag of its own — its real opt-in is installing the CI workflow
    AND enabling the matching ticket provider (``tickets.<provider>``) with the
    repo's ``owner``/``repo`` (with neither, no event ever reaches the engine and
    no repo resolves for one that does). So a deployment can route commands without
    the status mirror, mirror without routing, or run both.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Publish the live status comment onto every ticket a workspace works. Off leaves
    command routing untouched and writes nothing to your tracker.
    """

    admission: AdmissionLimits = Field(default_factory=AdmissionLimits)
    """Maximum retained assignment deliveries, including running work."""

    trigger: str = "@grove"
    """The mention that must open a comment for Grove to act, matched case insensitively at
    a word boundary.
    """

    allowed_actors: list[str] = Field(default_factory=list)
    """Logins allowed to drive issue ops regardless of repo permission. Write access always
    suffices, this only adds to it.
    """

    agent: str = "claude"
    """Which configured agent an issue ops created workspace spawns. Must name an entry in
    `agents`.
    """

    prompt_template: str = _DEFAULT_ISSUEOPS_PROMPT
    """The first prompt a ticket created workspace boots on, with `{title}`, `{body}`,
    `{number}`, `{url}`, `{comments}` and `{command_text}` filled from the ticket.
    """

    update_window_seconds: float = Field(default=5.0, ge=0)
    """At most one status comment edit per workspace per window, so a burst of activity
    never trips the forge's rate limit. `0` flushes every change.
    """

    deep_link_base_url: str = ""
    """Your dashboard's base URL. Set, the comment links to `{base}/w/{id}`. Empty omits
    the link.
    """

    assign_bot: bool = False
    """Assign the tracker's own account to every ticket a live workspace holds, so Grove's
    work is findable with the tracker's assignee filter. Released when the workspace
    ends.

    Assignment is an OUTPUT of Grove working a ticket and never an input: it is
    reconciled from the live fleet, and nothing here starts work. Turning this on
    cannot cause a workspace to be created — that is ``pickup_enabled``, a
    separate opt-in.

    The assignment is RELEASED when the workspace holding the ticket ends, so the
    board says who is working an issue now rather than who once did. Only
    assignments this daemon made are released — a ticket assigned to the bot by
    hand is left alone, and every other assignee is always untouched.
    """

    pickup_enabled: bool = False
    """Treat the assignee field as the work queue. Assign the bot to an open issue and the
    daemon starts a workspace for it, with no comment and no CI runner.
    """

    pickup_interval_seconds: float = Field(default=60.0, ge=5)
    """How often the pickup poll asks each tracker for its assigned issues."""

    pickup_max_active: int = Field(default=3, ge=1)
    """How many pickup started workspaces may run at once across the host. The rest wait
    for the next tick.
    """

    pickup_backoff_seconds: float = Field(default=300.0, ge=0)
    """How long a tracker is skipped after it fails or rate limits a poll. The next good
    poll clears it.
    """


# Which agent-state edges may fire a push. String values mirror
# ``AgentActivityState`` (waiting/blocked/error/idle) — kept a Literal here, not
# the imported enum, so ``config.py`` never imports ``grove.core.agents`` (which
# would cycle: agents → registry → adapters → config). The notifications
# subpackage coerces these strings back to the enum at its construction edge.
NotifyTransition = Literal["waiting", "blocked", "error", "idle"]
_DEFAULT_NOTIFY_ON: list[NotifyTransition] = ["waiting", "blocked", "error"]

# Workspace lifecycle events that may fire a push. Mirrors ``WorkspaceEvent.kind``
# (manager.py's ``EventKindStr``) for the subset the notifier phrases — same
# no-import discipline as ``NotifyTransition``. The default set is the
# *unexpected* half: a workspace that broke or was interrupted. The routine verbs
# (created/killed/paused/…) are available but off — a user-initiated pause needs
# no push back to the user who initiated it.
NotifyLifecycle = Literal[
    "error",
    "orphaned_detected",
    "offline_detected",
    "created",
    "killed",
    "paused",
    "resumed",
    "respawned",
]
_DEFAULT_NOTIFY_LIFECYCLE: list[NotifyLifecycle] = [
    "error",
    "offline_detected",
    "orphaned_detected",
]

# The channel-agnostic urgency a notification carries, mirroring
# ``notifications.channel.NotificationSeverity`` (config cannot import the
# notifications package — that package imports *this* one). Each channel maps
# these onto its own native scale below, in config: the mapping is policy, and
# policy does not belong in a branch.
NotifySeverity = Literal["low", "normal", "high", "urgent"]

# Gotify's dial is 0-10 and its Android client bins it: >=8 heads-up + sound,
# 4-7 vibrate, 1-3 silent, <=0 minimized. ntfy's is 1-5 (3 = default). The
# defaults below are chosen against those real thresholds so a pending question
# actually buzzes the phone and a routine pause never does.
_DEFAULT_GOTIFY_PRIORITIES: dict[NotifySeverity, int] = {
    "low": 2,
    "normal": 5,
    "high": 8,
    "urgent": 9,
}
_DEFAULT_NTFY_PRIORITIES: dict[NotifySeverity, int] = {
    "low": 2,
    "normal": 3,
    "high": 4,
    "urgent": 5,
}


class GotifyChannelConfig(BaseModel):
    """The Gotify push channel.

    ``server_url`` is the Gotify base (e.g. ``https://gotify.example.com``), and
    it also reads from ``GROVE_GOTIFY_API_URL`` so a deployment can point every
    repo at its own server without editing a file. ``token_env`` is the NAME of
    the env var holding the *application* token (Gotify's ``Axxx…``, the
    send-only kind), never the token itself — committed config stays secret-free,
    exactly like ``mewbo.api_key_env``.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Deliver through Gotify."""
    server_url: str = Field(default="", json_schema_extra={"x-env-var": "GROVE_GOTIFY_API_URL"})
    """Your Gotify server."""
    token_env: str = "GROVE_GOTIFY_TOKEN"
    """Name of the environment variable holding a Gotify application token, never the
    token.
    """
    priority: int = Field(default=5, ge=0, le=10)
    """Fallback priority for a severity with no entry in `priorities`."""

    priorities: dict[NotifySeverity, Annotated[int, Field(ge=0, le=10)]] = Field(
        default_factory=lambda: dict(_DEFAULT_GOTIFY_PRIORITIES)
    )
    """Severity to Gotify priority, 0 to 10. The defaults land on Gotify's own Android
    thresholds, so a question buzzes and a routine pause does not.
    """

    markdown: bool = True
    """Send the rich markdown body. Turn it off for a client that does not render
    CommonMark.
    """

    timeout_seconds: float = Field(default=5.0, gt=0)
    """Seconds to wait on one push before skipping it."""


class WebhookChannelConfig(BaseModel):
    """A generic JSON webhook channel speaking ntfy's publish format.

    POSTs the notification as JSON to ``url``. ntfy's JSON-publish API works
    directly: set ``topic`` and point ``url`` at the ntfy base. ``token_env`` (a
    NAME, never the secret) adds a ``Bearer`` header when set.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Deliver through the webhook."""
    url: str = ""
    """The URL to POST to."""
    token_env: str = ""
    """Name of the environment variable holding a bearer token, if the URL needs one."""
    topic: str = ""
    """ntfy topic, included only when set."""

    priorities: dict[NotifySeverity, Annotated[int, Field(ge=1, le=5)]] = Field(
        default_factory=lambda: dict(_DEFAULT_NTFY_PRIORITIES)
    )
    """Severity to priority on ntfy's 1 to 5 scale. Bounded, since ntfy rejects a value
    outside it.
    """

    timeout_seconds: float = Field(default=5.0, gt=0)
    """Seconds to wait on one POST before skipping it."""


class NotificationsConfig(BaseModel):
    """Push notifications when an agent needs you. Off by default.

    Three independent triggers, each with its own switch, all fanning out to
    every enabled channel:

    - ``on`` — a debounced rising edge into an agent state that wants the human:
      ``waiting`` (turn finished), ``blocked`` (awaiting input), ``error``.
      ``waiting`` additionally waits out ``waiting_quiet_minutes`` before it
      pushes — see that field.
    - ``on_question`` — the agent posted a question. Deduped by question id, not
      debounced: a second question inside the quiet window is a second thing the
      human must answer, and it is the one push that must never be dropped.
    - ``on_lifecycle`` — the workspace itself changed (it broke, it was
      orphaned, its session vanished).

    ``deep_link_base_url`` is the webapp base (e.g. ``https://grove.example.com``);
    a notification deep-links to ``{base}/w/{id}`` so tapping it opens that
    workspace. Mechanism, not policy: every value cascades like the rest of the
    config.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Send push notifications at all."""
    on: list[NotifyTransition] = Field(default_factory=lambda: list(_DEFAULT_NOTIFY_ON))
    """Agent states that fire a notification. `waiting`, `blocked`, `error` and `idle`.
    """
    on_question: bool = True
    """Push when the agent asks you something, with the prompt and its options. The one
    trigger every harness can produce.
    """

    on_lifecycle: list[NotifyLifecycle] = Field(
        default_factory=lambda: list(_DEFAULT_NOTIFY_LIFECYCLE)
    )
    """Workspace events that fire a notification, such as `error`, `orphaned_detected` and
    `offline_detected`. Routine verbs are off by default.
    """
    debounce_seconds: float = Field(default=30.0, ge=0)
    """Quiet window per workspace after a state fires, so one attention episode is one
    buzz. Questions dedupe by id instead.
    """

    waiting_quiet_minutes: float = Field(default=15.0, ge=0)
    """How long a session must stay settled before a `waiting` push fires, since a finished
    turn may still have a background command running. `0` fires as soon as every known
    tracker agrees nothing is left.
    """

    deep_link_base_url: str = "http://localhost:3000"
    """Where a tap lands, `{base}/w/{id}`. The default is the web app's own local origin,
    which a phone cannot reach, so set your reachable address or empty for no link.
    """

    gotify: GotifyChannelConfig = Field(default_factory=GotifyChannelConfig)
    """The Gotify push channel."""
    webhook: WebhookChannelConfig = Field(default_factory=WebhookChannelConfig)
    """A generic JSON webhook channel, speaking ntfy's publish format."""

    @property
    def deep_link_is_loopback(self) -> bool:
        """Whether the tap target is reachable only from this host.

        The feature's quietest failure: the push lands on your phone, you tap it,
        and the phone resolves ``localhost`` to *itself*. Nothing errors — the tap
        just dies, and the notification is worth nothing. The model can answer this
        about itself (pure, no I/O), so the broker's construction edge says it out
        loud instead of every operator discovering it on their lock screen. A LAN
        address is deliberately NOT loopback: a phone on the same network can
        follow it.
        """
        host = urlparse(self.deep_link_base_url).hostname or ""
        return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


_TELEMETRY_ENV_PREFIXES = ("OTEL_", "LANGFUSE_", "CLAUDE_CODE_")
"""Which variable names a telemetry env source may contribute to a launch.

A prefix allow-list rather than the whole file, because the file is a
credential store and an injected value is visible in the agent pane's
scrollback and in ``ps``. These three cover the OTel SDK's own vocabulary,
LangFuse's native trio, and the one runtime whose telemetry switches are
env-driven.
"""


ContentOwner = Literal["external", "grove"]
"""Who emits a session's prompt/response CONTENT to the tracing backend.

``external`` — the harness's own baseline emitter does (a Claude Code ``Stop``
hook, a tool's tracing plugin). ``grove`` — Grove reads the transcript and
emits the content itself, for a harness that has no baseline emitter.

A closed pair rather than a bare string because it drives a branch, and both
wrong answers are silent: two owners duplicate every turn under two trace
trees, none loses the content entirely.
"""

DEFAULT_CONTENT_OWNER: ContentOwner = "external"
"""Content belongs to the harness's own emitter unless config says otherwise.

The default falls this way because the baseline emitter must not depend on
Grove being installed, running, or healthy — that independence is the whole
point of leaving it in place, and it only holds if Grove stays out of its way
by DEFAULT rather than by successful detection.
"""


_OTEL_EXPORT_ENV: tuple[str, ...] = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    *(
        f"OTEL_EXPORTER_OTLP_{signal}_{knob}"
        for signal in ("TRACES", "METRICS", "LOGS")
        for knob in ("ENDPOINT", "HEADERS", "PROTOCOL")
    ),
    *(f"OTEL_{signal}_EXPORTER" for signal in ("TRACES", "METRICS", "LOGS")),
)
"""The OpenTelemetry exporter vocabulary: which exporter runs, and where it sends.

Enumerated rather than matched by prefix, because a prefix cannot separate
"where this stream goes" from the identity Grove stamps alongside it
(``OTEL_RESOURCE_ATTRIBUTES``) or from a trace context handed in
(``TRACEPARENT``) — both of which an agent may legitimately carry.
"""


_OWNED_CONTENT_ENV: dict[str, str] = {"OTEL_TRACES_EXPORTER": "none"}
"""What a Grove-owned-content launch forces on top of its reservation.

``none`` rather than clearing the variable: the OTel SDK's own default for an
unset exporter is ``otlp``, so silence would re-enable exactly the second,
inverted trace tree that naming Grove as the content owner exists to end.
"""


@dataclass(frozen=True, slots=True)
class TelemetryReservation:
    """One launch's resolution of Grove's claim on the agent's telemetry env.

    Pure data produced by :meth:`TelemetryConfig.reserve` and applied at the
    launch boundary, so "who owns this variable" is decided once, off any I/O,
    and the same answer feeds the composed environment and the log line that
    announces it.
    """

    env: dict[str, str]
    """Reserved variables Grove sets — the values that survive the launch merge."""

    unset: tuple[str, ...]
    """Reserved variables Grove does not set, so the launch must carry none."""

    displaced: tuple[str, ...]
    """Reserved variables whose already-configured value this launch replaces.
    NAMES only — a telemetry value can be a credential, so the record of what
    was taken over stays safe to log."""

    def apply(self, env: Mapping[str, str]) -> dict[str, str]:
        """`env` with the reservation enforced — the one place the claim bites.

        Both halves matter and neither is expressible as a merge: a reserved
        name Grove does not set is REMOVED (an agent-supplied exporter is not
        merely outranked, it is gone), and a reserved name Grove does set wins
        over every other layer, including ``agents[].env``, which outranks
        Grove everywhere else in the launch environment.
        """
        dropped = set(self.unset)
        return {**{key: value for key, value in env.items() if key not in dropped}, **self.env}


TelemetryContent = Literal["none", "messages", "all"]
"""How much normalized transcript content an explicit backfill may export."""


class TelemetryBackfillConfig(BaseModel):
    """Consent and profile selection for exporting historical sessions.

    Empty by default: usage discovery may index every reachable transcript,
    while an external telemetry write must name the exact provider roots it is
    allowed to read and export. Values use the same config-dir roots as agent
    profiles (for example ``~/.codex``), keeping one profile vocabulary.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Allow `grove usage backfill --telemetry` to export historical sessions at all."""
    profiles: dict[AgentKind, tuple[str, ...]] = Field(default_factory=dict)
    """Provider config roots whose transcripts may be exported, such as `~/.codex`."""
    content: TelemetryContent = "none"
    """How much content a backfilled trace carries. `none` exports structure only."""


class TelemetryReceiverConfig(BaseModel):
    """The daemon's own OTLP endpoint for a harness's native exporter.

    Grove's other telemetry tiers describe a session from the outside — a
    frozen launch-time identity stamp and a replayed transcript. This is the
    one tier that can carry what only the harness's own exporter knows: Claude
    Code's beta ``llm_request`` span, for instance, is the sole place
    time-to-first-token appears at all — no transcript records it. Off by
    default, because mounting an HTTP endpoint on the daemon is a decision an
    operator makes deliberately; it is not a side effect of turning on the
    rest of this section.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Mount the receiver on the daemon. Off serves no endpoint."""

    path: str = "/otlp"
    """Where the receiver is mounted under the daemon's address. Point a harness's
    `OTEL_EXPORTER_OTLP_ENDPOINT` at it.
    """

    queue_capacity: int = 256
    """Export batches held before the receiver starts shedding.

    Sized in batches, not spans, because a batch is the unit a sender retries.
    A burst beyond this capacity is dropped and logged rather than queued
    without bound — an unbounded queue would let a telemetry spike take the
    daemon down with it.
    """

    workers: int = 2
    """Threads that transform accepted batches off the daemon's event loop.

    The work is CPU-bound protobuf walking, so more workers smooth latency
    rather than raise throughput; they exist to keep this work off the loop
    that also serves every dashboard and SSE stream, not to parallelize it.
    """


class TelemetryExportConfig(BaseModel):
    """Capacity and retry limits for acknowledged span export."""

    model_config = _FROZEN

    admission: AdmissionLimits = Field(
        default_factory=lambda: AdmissionLimits(max_items=2048, max_bytes=8 * 1024 * 1024)
    )
    batch_size: int = Field(default=512, gt=0)
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_backoff_seconds: float = Field(default=1.0, ge=0)
    max_retry_backoff_seconds: float = Field(default=30.0, ge=0)

    @model_validator(mode="after")
    def _bounds_agree(self) -> TelemetryExportConfig:
        if self.batch_size > self.admission.max_items:
            raise ValueError("export batch_size exceeds admission.max_items")
        if self.retry_backoff_seconds > self.max_retry_backoff_seconds:
            raise ValueError("export retry backoff exceeds maximum")
        return self


class TelemetryConfig(EnvSourceConfig):
    """Langfuse credentials and OpenTelemetry passthrough. The three credential fields hold
    variable names, never secrets, so committed config stays publishable.

    Secret-free like every other integration submodel (the ``mewbo.api_key_env``
    discipline): the three canonical values are env-var NAMES, never secret
    literals, so committed config stays publishable — the actual host/keys live
    only in the consuming process's environment. Off by default (mechanism, not
    policy); a deployment opts in by setting ``enabled: true`` and pointing the
    three ``*_env`` fields at whatever names its host actually exports.

    **Where those three values come from is the inherited
    :class:`EnvSourceConfig` question, and for telemetry it is usually the
    answer.** Grove's own process is the exporter here, and the process that
    launches agents is typically a long-lived daemon started by a service
    manager: it read its environment once, at exec, and nothing exported in a
    shell afterwards can reach it. So a deployment that only exports the keys
    interactively gets a config that says ``enabled: true`` and exports
    nothing — silently. Point ``telemetry.env_file`` at a dotenv holding the
    three variables (``~/.config/grove/langfuse.env`` is the conventional
    place) and every launch re-reads it, including after a key rotation::

        {"telemetry": {"enabled": true, "env_file": "~/.config/grove/langfuse.env"}}

    Set that in your user config or ``.grove/config.local.json``: a committed
    layer may only name a path inside the repository, and may not run an
    ``env_command`` at all (:class:`CommittedEnvSource`).
    """

    model_config = _FROZEN

    SECTION: ClassVar[str] = "telemetry"
    export: TelemetryExportConfig = Field(default_factory=TelemetryExportConfig)

    enabled: bool = False
    """Export Grove's own spans to the configured backend."""

    backfill: TelemetryBackfillConfig = Field(default_factory=TelemetryBackfillConfig)
    """Consent and profile selection for exporting historical sessions."""

    receiver: TelemetryReceiverConfig = Field(default_factory=TelemetryReceiverConfig)
    """The daemon's own OTLP endpoint for a harness's native exporter. Independent of
    `enabled`.
    """

    host_env: str = "LANGFUSE_HOST"
    """Name of the variable holding the Langfuse host, never the URL itself. Rename it here
    if your secret store exports a different name.
    """

    public_key_env: str = "LANGFUSE_PUBLIC_KEY"
    """Name of the variable holding the Langfuse public key."""

    secret_key_env: str = "LANGFUSE_SECRET_KEY"
    """Name of the variable holding the Langfuse secret key, never the secret itself."""

    passthrough_kinds: tuple[AgentKind, ...] = ("claude_code", "codex")
    """Agent kinds that receive the derived telemetry environment at launch. `mewbo` runs
    server side and `generic` has nothing to instrument, so both are out by default.
    """

    reserved_env: tuple[str, ...] = _OTEL_EXPORT_ENV
    """OTLP exporter variables Grove owns for every agent it launches, so an agent's own
    exporter cannot split one session into two traces. A displaced value is logged by
    name.

    Grove composes an agent's launch environment, so it already decides whether
    that agent exports at all. These names make the decision total: for a
    runtime in ``passthrough_kinds``, each one is either set by Grove or carried
    by nobody, and a value arriving from anywhere else — ``agents[].env``, or
    the environment Grove itself was started with — does not reach the agent.
    It is replaced rather than honoured, and every replaced NAME is logged at
    the launch that replaced it, so the change is never silent.

    **A workspace's telemetry destination is configured through Grove**, in this
    section: ``env_file`` / ``env_command`` may name any of these variables and
    whatever they carry stands, which is how a deployment points its workspaces
    at its own collector. Pointing the agent's own exporter somewhere Grove does
    not control is what this ends — one tap per workspace, so a trace tree does
    not arrive twice under two unrelated roots.

    The default is the OpenTelemetry exporter vocabulary: which exporter runs
    per signal, and where it sends. Grove's identity stamp
    (``OTEL_RESOURCE_ATTRIBUTES``) and an inbound trace context are deliberately
    absent — they say who the agent is, not where its telemetry goes. Narrow the
    tuple to hand a name back to the agent; empty it to reserve nothing.
    """

    content_owner: dict[AgentKind, ContentOwner] = Field(default_factory=dict)
    """Which side emits each runtime's prompt and response content, per agent kind, so a
    turn is never recorded twice or not at all.

    **Exactly one owner per session, and it is chosen HERE.** Grove always emits
    the context (workspace, branch, ticket, agent identity); content is the half
    two producers can both reach, because a harness's own baseline emitter and
    Grove read the very same transcript. Two owners means every turn appears
    twice under two unrelated trace trees; no owner means the words are simply
    absent. Neither shows up as an error anywhere.

    Keyed by agent kind because the harnesses genuinely differ — one ships a
    baseline emitter today, another may never have one — so a single global
    switch could only ever be right for one of them. A kind that is not named
    here resolves to ``external``, which is what keeps that baseline emitter
    working when Grove is absent or has crashed. Name a kind ``grove`` only
    where nothing else emits content for it::

        {"telemetry": {"content_owner": {"codex": "grove"}}}

    Deliberately NOT inferred. Grove cannot see another process's hook or plugin
    without probing across a boundary it does not own, and such a probe is wrong
    in both directions — a false positive drops all content, a false negative
    doubles it. Configuration is the one answer that is auditable, so
    ``grove doctor`` renders what this RESOLVES to rather than what it detects.

    **This and ``reserved_env`` are one policy, not two knobs.** Naming a kind
    ``grove`` says Grove's replay IS that runtime's trace, so the launch also
    stops switching the runtime's own exporter on and forces its trace exporter
    off — otherwise the same session arrives twice, once as Grove's tree and
    once as the harness's inverted one, and the second carries no content a
    traces-only backend can read. Move a kind back to ``external`` to hand its
    own exporter back.
    """

    def content_owner_for(self, kind: AgentKind) -> ContentOwner:
        """Who owns this runtime's content — the single answer, pure.

        The one seam a content emitter gates on, so "am I allowed to emit this"
        is asked in exactly one vocabulary no matter which producer is asking.
        Unnamed kinds resolve to :data:`DEFAULT_CONTENT_OWNER`; a map without a
        default would push "and what if it says nothing" into every caller,
        which is where the two owners would drift apart.
        """
        return self.content_owner.get(kind, DEFAULT_CONTENT_OWNER)

    def owns_content(self, kind: AgentKind) -> bool:
        """Does GROVE emit this runtime's content — the reservation's own read.

        A predicate rather than a comparison at each call site, because two
        places act on the answer (the launch stops enabling the runtime's native
        exporter, and :meth:`reserve` forces its trace exporter off) and one of
        them drifting is exactly the "two knobs that contradict each other" this
        exists to prevent.
        """
        return self.content_owner_for(kind) == "grove"

    def reserves(self, kind: AgentKind) -> bool:
        """Is Grove the tap for this runtime's launches?

        Only for a kind Grove actually derives telemetry env for: with the
        section disabled, or a runtime left out of ``passthrough_kinds``, Grove
        exports nothing on that agent's behalf and taking its variables away
        would leave it unable to export at all — a reservation that delivers
        silence instead of ownership.
        """
        return self.enabled and kind in self.passthrough_kinds

    def reserve(
        self, kind: AgentKind, *, grove: Mapping[str, str], claimed: Mapping[str, str]
    ) -> TelemetryReservation:
        """Resolve Grove's claim on one launch's telemetry env — pure.

        `grove` is everything Grove itself derived for this launch (this
        section's :meth:`derive_env` plus the runtime's own exporter switch);
        `claimed` is every value that would otherwise have been in force — the
        agent's ``env`` over the environment Grove is running under. Both are
        supplied by the caller, so the decision is testable without a process
        environment, and the loud part (the log) happens at that boundary.

        The rule that changes here is precedence: everywhere else in a launch
        ``agents[].env`` is the most specific layer and wins, and for these
        names it does not — "explicit wins" cannot survive a reservation, since
        the value being reserved is precisely the one an agent would otherwise
        set for itself.
        """
        if not self.reserves(kind):
            return TelemetryReservation(env={}, unset=(), displaced=())
        env = {name: grove[name] for name in self.reserved_env if grove.get(name)}
        if self.owns_content(kind):
            env.update(
                {
                    name: value
                    for name, value in _OWNED_CONTENT_ENV.items()
                    if name in self.reserved_env
                }
            )
        return TelemetryReservation(
            env=env,
            unset=tuple(name for name in self.reserved_env if name not in env),
            displaced=tuple(
                name
                for name in self.reserved_env
                if claimed.get(name) and claimed[name] != env.get(name)
            ),
        )

    def reserved_unset(self, kind: AgentKind, env: Mapping[str, str]) -> tuple[str, ...]:
        """Reserved names a launch must CLEAR out of the environment it inherits.

        The other half of :meth:`TelemetryReservation.apply`, which can only
        correct the environment Grove composes: an agent's process also inherits
        whatever the pane carries, and an endpoint that leaks in that way points
        the agent at a backend as effectively as one written in config. Read off
        the ALREADY-RESERVED env, so the two halves cannot disagree about which
        names Grove ended up setting.
        """
        if not self.reserves(kind):
            return ()
        return tuple(name for name in self.reserved_env if name not in env)

    def _source_names(self) -> tuple[tuple[str, str], ...]:
        """``(derived var, configured source-var NAME)`` for the canonical three.

        One table, three readers (the derivation, its warning, and
        :meth:`unresolved`), so "which name feeds which output" cannot drift
        between what Grove exports and what it reports as missing.
        """
        return (
            ("LANGFUSE_HOST", self.host_env),
            ("LANGFUSE_PUBLIC_KEY", self.public_key_env),
            ("LANGFUSE_SECRET_KEY", self.secret_key_env),
        )

    def unresolved(self, derived: Mapping[str, str]) -> tuple[str, ...]:
        """Which configured variable NAMES produced nothing in a :meth:`derive_env`.

        Pure, and the inverse of the derivation rather than a second copy of it:
        a name is unresolved exactly when the value it feeds is absent from the
        result. Names only — a credential never crosses this boundary — which is
        what makes the answer safe to log and to render in ``grove doctor``.
        """
        return tuple(name for key, name in self._source_names() if key not in derived)

    def _source_env(self, repo_root: Path | None) -> Mapping[str, str]:
        """This section's configured ``env_file`` / ``env_command``, read NOW.

        Best-effort by contract: this runs on the launch path, where telemetry is
        a convenience and a workspace that starts untraced is enormously better
        than one that does not start. A failed resolution is therefore WARNED and
        treated as empty rather than raised — the opposite call from the
        container arm, whose credentials are what the workspace is for.

        The import is call-time-local because ``env_source`` imports this module;
        the cheap path (no source configured, the default) never reaches it.
        """
        if not (self.env_file or self.env_command):
            return {}
        from grove.core.env_source import EnvSource  # noqa: PLC0415

        try:
            return EnvSource.resolve(self, repo_root=repo_root or Path.cwd()).values
        except EnvSourceError as exc:
            logger.warning(
                "telemetry: env source unusable, falling back to the process env: {}", exc
            )
            return {}

    def derive_env(
        self, env: Mapping[str, str], *, repo_root: Path | None = None
    ) -> dict[str, str]:
        """Derive the launch-env vars from the canonical three, read out of `env`.

        The caller supplies the base mapping — never `os.environ` read here —
        so this composes with the launch boundary's own env resolution
        (`LaunchSpec.env`). On top of it sits this section's configured env
        source (:meth:`_source_env`), which wins: an operator who points
        ``telemetry.env_file`` at a dotenv is saying "read them from here", and
        the value baked into a daemon's environment at exec is precisely the one
        that cannot be updated. `repo_root` is what a repo-relative `env_file`
        resolves against; without one a relative path can only mean the
        process's own cwd.

        **An enabled-but-unresolved config warns, once per call, naming the
        variables that failed** — never their values. Silence here is the defect
        this loudness exists to close: telemetry that derives nothing produces no
        endpoint, so the agent's exporter is never switched on and every span is
        dropped, with the config still reading ``enabled: true``. Nothing raises:
        this is called while composing a launch.

        Returns BOTH derivable shapes at once and lets the launch boundary pick
        per `passthrough_kinds`:

        1. the native Langfuse SDK trio (`LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY`
           / `LANGFUSE_SECRET_KEY`) — for a runtime whose own code reads these
           directly;
        2. the generic OTEL exporter pair — `OTEL_EXPORTER_OTLP_ENDPOINT`
           (`{host}/api/public/otel`) and `OTEL_EXPORTER_OTLP_HEADERS`
           (`Authorization=Basic <b64(public:secret)>,x-langfuse-ingestion-version=4`)
           — for a runtime that only speaks OTLP. The header is assembled here
           at call time and never stored — only the three source values are
           config.

        A name that resolves to nothing is omitted (a partial credential set
        derives whatever it can); the OTEL pair needs all three source values,
        so it's only emitted when all resolve. Disabled (`enabled=False`)
        always derives nothing, and warns nothing — opting out is not a fault.
        """
        if not self.enabled:
            return {}

        source = self._source_env(repo_root)
        lookup = {**env, **source}

        # Everything telemetry-shaped the operator put in their OWN env source
        # rides through verbatim and wins over anything derived below. This is
        # what makes "the export works the same however the agent was started"
        # achievable at all: a deployment that has already proved a working
        # pipeline — a collector endpoint, a protocol, which exporters are on,
        # which content knobs are set — hands Grove that exact configuration
        # instead of Grove inventing a parallel one that has to be kept in
        # agreement by hand. Grove derives only what the source did not say.
        #
        # Scoped to the telemetry prefixes rather than passing the file through
        # wholesale: an injected value lands in the pane's scrollback and in
        # `ps` for the same uid, so a credential that happens to share the file
        # must not reach an agent that had no use for it.
        passthrough = {
            key: value
            for key, value in source.items()
            if key.startswith(_TELEMETRY_ENV_PREFIXES) and value
        }
        derived = {key: lookup[name] for key, name in self._source_names() if lookup.get(name)}

        missing = self.unresolved(derived)
        if missing:
            logger.warning(
                "telemetry is enabled but {} carry no value, so nothing is exported — "
                "set them in the environment Grove itself runs in, or point "
                "telemetry.env_file at a dotenv holding them",
                ", ".join(f"'{name}'" for name in missing),
            )

        if not missing:
            public_key = derived["LANGFUSE_PUBLIC_KEY"]
            secret_key = derived["LANGFUSE_SECRET_KEY"]
            host = derived["LANGFUSE_HOST"]
            token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
            derived["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"{host.rstrip('/')}/api/public/otel"
            derived["OTEL_EXPORTER_OTLP_HEADERS"] = (
                f"Authorization=Basic {token},x-langfuse-ingestion-version=4"
            )
        # Passthrough last: a derived endpoint is Grove's best guess at where
        # this Langfuse lives, while a named one is where the operator's own
        # traces demonstrably arrive — including a collector in front of it,
        # which Grove cannot infer from credentials.
        return {**derived, **passthrough}


def _default_proxy_upstreams() -> dict[AgentKind, str]:
    return {"claude_code": "https://api.anthropic.com", "codex": "https://api.openai.com/v1"}


def _default_proxy_base_url_env() -> dict[AgentKind, str]:
    return {"claude_code": "ANTHROPIC_BASE_URL", "codex": "OPENAI_BASE_URL"}


class ProxyConfig(BaseModel):
    """A loopback proxy in front of the LLM gateway for wire level capture.

    Off by default (mechanism, not policy). When a deployment opts in and the
    orchestrator serves the proxy (``grove.core.proxy.ProxyApp``), an agent is
    pointed at it through :meth:`proxy_env` at the launch boundary and every
    provider request/response is forwarded VERBATIM while telemetry (true TTFT,
    token usage, latency) is teed off the stream. Nothing here holds a secret —
    upstreams are public API base URLs and the env-var NAMES that carry the
    proxy address to each runtime; auth flows untouched through the proxy, never
    into config.

    Two per-kind maps do the wiring, keyed by ``AgentKind`` (the map keys are the
    opt-in set, the ``TelemetryConfig.passthrough_kinds`` analogue expressed as
    membership): ``upstreams`` = where the proxy forwards that kind's traffic;
    ``base_url_env`` = the env var whose value :meth:`proxy_env` sets to the proxy
    URL (``claude_code`` reads ``ANTHROPIC_BASE_URL``; ``codex`` reads its
    ``model_providers`` base-url env, ``OPENAI_BASE_URL`` by default). Both
    cascade and merge like every other config value.
    """

    model_config = _FROZEN

    enabled: bool = False
    """Route agent traffic through Grove's loopback proxy for wire level capture. Off
    leaves launch env and traffic untouched.
    """

    host: str = "127.0.0.1"
    """Loopback interface the proxy listens on. Never a routable address, since it relays
    provider credentials.
    """

    port: int = Field(default=8788, ge=1, le=65535)
    """Port the proxy listens on."""

    upstreams: dict[AgentKind, str] = Field(default_factory=_default_proxy_upstreams)
    """Per agent kind real upstream base URL. A kind absent here is not proxied."""

    base_url_env: dict[AgentKind, str] = Field(default_factory=_default_proxy_base_url_env)
    """Per agent kind name of the variable that points that runtime at the proxy, such as
    `ANTHROPIC_BASE_URL` for Claude Code.
    """

    log_bodies: bool = False
    """Capture request bodies into the telemetry event. Off by default because a body can
    hold prompt content. Response bodies and headers are never captured.
    """

    max_body_bytes: int = Field(default=8192, ge=0)
    """Cap on a captured request body."""

    def proxy_env(self, kind: AgentKind, env: Mapping[str, str] | None = None) -> dict[str, str]:
        """Derive the launch-env that points a ``kind`` agent at the proxy.

        The proxy sibling of ``TelemetryConfig.derive_env``: returns the
        ``{env_var_name: proxy_url}`` the launch boundary merges into an agent's
        env so its provider client dials the loopback proxy instead of the real
        upstream (Claude honors ``ANTHROPIC_BASE_URL``; Codex its
        ``model_providers`` base-url env). Disabled, or a kind with no configured
        ``base_url_env`` entry, derives nothing. The proxy URL is the same
        ``host``/``port`` for every kind — a deployment fronting multiple
        providers on distinct ports overrides at the orchestration seam.

        **Grove's proxy and an existing gateway are mutually exclusive, and the
        collision is resolved in the gateway's favour, loudly.** That variable is
        not Grove's to claim: a deployment that already routes provider traffic
        through its own gateway declares that by exporting the very name this
        would set, and quietly overwriting it moves every request onto a
        different route with nothing said — an invisible change to where
        credentials are sent and to how the account is billed. So an inherited
        value is left in place and warned about, naming the variable but never
        its value (a base URL may carry userinfo). Pick one: unset the ambient
        variable, or leave ``proxy.enabled`` false.

        *env* is the environment the agent will actually launch with; omitted, it
        is the process's own — the launch boundary composes on top of
        ``os.environ``, so that is the ambient declaration this has to see.
        """
        if not self.enabled:
            return {}
        env_name = self.base_url_env.get(kind)
        if not env_name:
            return {}
        proxy_url = f"http://{self.host}:{self.port}"
        inherited = (env if env is not None else os.environ).get(env_name, "").strip()
        if inherited and inherited != proxy_url:
            logger.warning(
                "proxy.enabled is set, but '{}' already points {} at a gateway of its own — "
                "leaving it untouched and forwarding nothing through Grove's proxy. Unset "
                "that variable to use Grove's proxy, or set proxy.enabled to false.",
                env_name,
                kind,
            )
            return {}
        return {env_name: proxy_url}


class ModelsConfig(BaseModel):
    """How a model id READS in a picker, as distinct from what it costs.

    A provider id is an address, not a name: a gateway publishes
    ``anthropic-gpt-5.6-luna`` and every catalog endpoint measured on the
    reference host publishes no display name beside it. So the name is declared
    here rather than derived — an operator names the models their fleet actually
    runs, and a model nobody named keeps its id.

    Display only. Nothing here ever changes what is sent to a provider, which is
    what keeps a rename safe for a workspace already pinned to the id.
    """

    model_config = _FROZEN

    display_names: dict[str, str] = Field(default_factory=dict)
    """Model id to the name a picker prints for it. Exact match only, because a prefix
    rule would silently name a model the operator never saw. An id with no entry keeps
    its own spelling rather than being guessed at.
    """

    @field_validator("display_names")
    @classmethod
    def _nonblank_display_names(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not model.strip() or not name.strip() for model, name in value.items()):
            raise ValueError("model display names must not contain blank ids or names")
        return value

    def display_name(self, model_id: str) -> str | None:
        """The declared name for ``model_id``, or ``None`` when none is declared.

        ``None`` means *nobody named this*, which a client renders as the id
        itself. It is deliberately not a fallback computed here: the clients
        already share one pure labelling adapter, and a second spelling rule in
        the engine is how two surfaces come to print one model two ways.
        """
        return self.display_names.get(model_id)


class ModelPriceConfig(BaseModel):
    """What one model's tokens cost, per million tokens.

    A missing rate is distinct from an explicit free rate. Manual configurations
    retain zero defaults for compatibility; source snapshots use ``None`` when a
    provider did not report a particular rate.
    """

    model_config = _FROZEN

    input: float | None = 0.0
    """Per million fresh input tokens."""

    output: float | None = 0.0
    """Per million output tokens."""

    cache_read: float | None = 0.0
    """Per million tokens served from the prompt cache, usually a fraction of `input`.
    """

    cache_write: float | None = 0.0
    """Per million tokens written into the prompt cache, usually a premium over `input`.
    """

    @field_validator("input", "output", "cache_read", "cache_write")
    @classmethod
    def _valid_rate(cls, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("price rates must be finite and nonnegative")
        return value


class UsagePricingSourceConfig(BaseModel):
    """One authenticated LiteLLM compatible model info endpoint."""

    model_config = _FROZEN

    base_url: str
    """The service root or its `/v1` root. Grove reads `model/info` under it."""

    token_env: str
    """Name of the environment variable holding this source's bearer token."""

    timeout_seconds: float = 10.0
    """Bound on one model info request."""

    @field_validator("base_url")
    @classmethod
    def _valid_base_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("pricing source base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "pricing source base_url must not carry credentials, query, or fragment"
            )
        return value.rstrip("/")

    @field_validator("token_env")
    @classmethod
    def _valid_token_env(cls, value: str) -> str:
        if not value or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("pricing source token_env must be an environment variable name")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _valid_timeout(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("pricing source timeout_seconds must be finite and positive")
        return value


class UsagePricingConfig(BaseModel):
    """Model prices used to estimate cost from token counts.

    Manual entries take precedence over source snapshots. Sources are opt-in so
    the default has no network behavior.
    """

    model_config = _FROZEN

    currency: str = "USD"
    """ISO 4217 code the prices are quoted in."""

    models: dict[str, ModelPriceConfig] = Field(default_factory=dict)
    """Manual prices by model id, exact match first then longest prefix. They override
    fetched entries.
    """

    sources: list[UsagePricingSourceConfig] = Field(default_factory=list)
    """Authenticated pricing endpoints. Empty disables remote pricing, and a higher layer
    replaces the list.
    """

    aliases: dict[str, str] = Field(default_factory=dict)
    """Model id to the id it is priced as. Explicit only, Grove never infers one from a
    prefix.
    """

    cache_ttl_seconds: float = 3600.0
    """Maximum age of a fetched price snapshot before it is refreshed."""

    _fetched_models: frozenset[str] = PrivateAttr(default_factory=frozenset)

    @field_validator("currency")
    @classmethod
    def _valid_currency(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("pricing currency must be a three-letter uppercase ISO 4217 code")
        return value

    @field_validator("aliases")
    @classmethod
    def _nonblank_aliases(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not source.strip() or not target.strip() for source, target in value.items()):
            raise ValueError("pricing aliases must not contain blank model ids")
        return value

    @field_validator("cache_ttl_seconds")
    @classmethod
    def _valid_cache_ttl(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("pricing cache_ttl_seconds must be finite and nonnegative")
        return value

    @model_validator(mode="after")
    def _aliases_are_acyclic(self) -> UsagePricingConfig:
        if self.sources and self.currency != "USD":
            raise ValueError("LiteLLM pricing sources quote USD; currency must be USD")
        if any(not key.strip() or not value.strip() for key, value in self.aliases.items()):
            raise ValueError("pricing alias names and targets must not be blank")
        for start in self.aliases:
            seen: set[str] = set()
            model = start
            while model in self.aliases:
                if model in seen:
                    raise ValueError("pricing aliases must not contain a cycle")
                seen.add(model)
                model = self.aliases[model]
        return self

    def with_fetched_models(
        self, models: dict[str, ModelPriceConfig], fetched_models: frozenset[str]
    ) -> UsagePricingConfig:
        """Return an internal price snapshot while retaining source provenance."""
        result = self.model_copy(update={"models": models})
        object.__setattr__(result, "_fetched_models", fetched_models)
        return result

    @property
    def fetched_models(self) -> frozenset[str]:
        """Exact-only source entries in the internal catalog snapshot."""
        return self._fetched_models


class UsageQuotaGatewayConfig(BaseModel):
    """One aggregate quota endpoint holding subscriptions from several vendors.

    The bearer token stays in the consuming process environment; configuring an
    endpoint never puts a credential into a cascadeable file. An empty URL leaves
    this optional source inactive.
    """

    model_config = _FROZEN

    base_url: str = ""
    """The gateway endpoint fetched once per quota refresh."""

    token_env: str = "GROVE_QUOTA_GATEWAY_TOKEN"
    """Name of the environment variable holding the gateway bearer token."""


class UsageQuotaConfig(BaseModel):
    """How subscription windows are collected per account.

    Grove reads each tool's OWN credential store at refresh time and calls the
    endpoint that credential selects. It never copies a secret into Grove's
    config or database, never refreshes or rewrites a provider credential, and
    never sends usage anywhere but that provider. A collection failure degrades
    the one account it belongs to and never affects launching an agent.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Collect quota for the selected `profiles`."""

    profiles: dict[
        Literal["claude_code", "codex"], tuple[Annotated[str, Field(min_length=1)], ...]
    ] = Field(default_factory=dict)
    """Subscription profiles whose quota may be collected and displayed.

    Empty is the safe default: Grove still indexes activity from every profile,
    but shows no subscription quota until the operator lists roots under their
    provider. The provider-keyed map cascades naturally and a higher layer can
    clear one provider with an empty list. Duplicate or symlinked roots collapse
    to one account id, so one subscription is never shown twice. Credentials
    are read in place and never copied into config or the usage database.
    """

    ttl_seconds: int = Field(default=900, ge=30)
    """How long a good reading is served before the provider is asked again.

    Every Grove process on this host shares one probe ledger, so a dashboard, a
    terminal UI and a `grove usage` run inside one window together cost one
    request rather than three. Fifteen minutes still samples a five-hour window
    twenty times and a weekly one hundreds of times, while pages are opened far
    more often than either moves. A provider whose quota is a file its own tool
    already wrote is not governed by this at all: there is no budget to protect
    there, and holding its answer back would only make it older.
    """

    retry_floor_seconds: int = Field(default=120, ge=10)
    """Wait after the first auth or rate limit failure, doubling per failure, while the
    last good reading keeps rendering as stale.
    """

    retry_max_seconds: int = Field(default=3600, ge=60)
    """Ceiling on that doubling."""

    timeout_seconds: float = Field(default=10.0, gt=0)
    """Bound on each credential read and provider request."""

    labels: dict[str, str] = Field(default_factory=dict)
    """Account id to display name, so two profiles are distinguishable without Grove
    storing an email.
    """

    gateway: UsageQuotaGatewayConfig = Field(default_factory=UsageQuotaGatewayConfig)
    """One aggregate quota endpoint holding subscriptions from several vendors."""

    window_seconds: dict[str, int] = Field(default_factory=dict)
    """Window label to its length, for providers that publish a reset instant but never the
    window's length.

    A burn rate needs a window START, and a start is only derivable from a reset
    plus a duration. Anthropic's usage endpoint reports the reset and no
    duration, so without this every Claude window reads `unknown` however much
    of it has been spent. Empty by default and consulted ONLY where the provider
    said nothing: Grove will not assert a boundary on a provider's behalf,
    because these have moved before and a guessed denominator produces a
    confident projection off a number nobody published. Naming one here is you
    asserting it, which is a different thing from Grove assuming it.
    """

    burn_tight_percent: float = Field(default=85.0, gt=0)
    """Projected usage at reset above which a window reads `tight`. Grove extrapolates the
    pace so far and contacts no provider.
    """

    burn_over_percent: float = Field(default=100.0, gt=0)
    """Projected usage at reset above which a window reads `over`, on course to run out
    before it resets.
    """

    @model_validator(mode="after")
    def _burn_thresholds_are_ordered(self) -> UsageQuotaConfig:
        """`tight` must not sit above `over`, or the band between them is unreachable."""
        if self.burn_tight_percent > self.burn_over_percent:
            raise ValueError("usage.quota.burn_tight_percent must not exceed burn_over_percent")
        return self


# --- shell-command attribution (usage.commands) ----------------------------
class UsageCommandsConfig(BaseModel):
    """How a shell tool call's time is attributed to the command that led it.

    A `Bash` call carries a whole shell line, so the audit records the leading
    top-level executable and reports *time in shell calls led by X* — never
    *time spent in X*, which no measurement here can support. These knobs
    control how that executable's name is folded before it is ranked.
    """

    model_config = _FROZEN

    basename: bool = True
    """Fold an absolute path to its final component, so `/usr/bin/git` and `git` are one
    row.
    """

    version_suffix_pattern: str = r"(?<=[A-Za-z])\d+(?:\.\d+)+$"
    """Trailing version fragment stripped from a name, so `python3.12` counts as `python`.
    """

    aliases: dict[str, str] = Field(
        default_factory=lambda: {
            "egrep": "grep",
            "fgrep": "grep",
            "python2": "python",
            "python3": "python",
            "pip3": "pip",
        }
    )
    """Executable names folded onto one row.

    Three collapses a reader often expects are deliberately absent, because
    each would hide the cost it exists to reveal. `npm`/`npx` and `rg`/`grep`
    are different programs with different performance. And `uv run python …`
    is credited to `uv`, not `python`, because uv's own dependency resolution
    is real time the invocation spent — folding it onto the interpreter
    reports that overhead as if the code had been running.
    """

    censored_at_ms: int = Field(default=600_000, ge=0)
    """A duration at or above this is reported as censored rather than as cost, since it
    marks the harness's own timeout rather than the command's length. `0` disables it.
    """

    @model_validator(mode="after")
    def _version_suffix_compiles(self) -> UsageCommandsConfig:
        """Reject a bad pattern at load, not at the first indexed command.

        This value is consumed deep inside a refresh; an `re.error` raised
        there degrades the whole usage engine to a 501 that names a regex.
        """
        try:
            re.compile(self.version_suffix_pattern)
        except re.error as exc:
            raise ValueError(
                f"usage.commands.version_suffix_pattern is not a valid regex: {exc}"
            ) from exc
        return self


# --- end shell-command attribution -----------------------------------------


class UsageInsightsConfig(BaseModel):
    """Thresholds for the usage audit's detectors.

    Defaults are conservative: a detector would rather stay silent than rank a
    normal working pattern as a problem. Every one of these is config because a
    threshold baked into code is policy nobody can disagree with.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Run the detectors at all."""

    min_occurrences: int = Field(default=3, ge=2)
    """How often a pattern must recur before it is reported."""

    retry_window_seconds: int = Field(default=300, ge=1)
    """Repeats of the same failing call inside this window count as one retry loop."""

    edit_churn_edits: int = Field(default=4, ge=2)
    """Edits to one file in a session before it is called churn."""

    slow_operation_ms: int = Field(default=60_000, ge=1)
    """A generation or tool call above this is a slow operation candidate."""

    concentration_share: float = Field(default=0.5, gt=0, le=1)
    """Share of tokens or time one project, account or model must hold before concentration
    is named.
    """


class UsageConfig(BaseModel):
    """The usage audit. Indexing, pricing, quota and insights.

    Reads the agent transcripts Grove can already reach and projects them into a
    private local cache so historical questions answer quickly. The cache holds
    metrics and metadata only: no prompt, response, reasoning, tool-result body
    or credential is ever written to it. It is derived, so deleting it is always
    safe and rebuilds on the next refresh.
    """

    model_config = _FROZEN

    enabled: bool = True
    """Index transcripts into the usage audit at all."""

    retention_days: int | None = None
    """Drop indexed events older than this on refresh. Unset keeps everything."""

    max_sessions_per_page: int = Field(default=100, ge=1, le=1000)
    """Upper bound on one page of the session table."""

    max_breakdown_rows: int = Field(default=50, ge=1, le=500)
    """Rows per breakdown dimension before the long tail is dropped. The response says when
    it truncated.
    """

    busy_timeout_ms: int = Field(default=5000, ge=0)
    """How long a writer waits on the SQLite lock, since the daemon, the TUI and a backfill
    can all write at once. `0` raises immediately.
    """

    pricing: UsagePricingConfig = Field(default_factory=UsagePricingConfig)
    """Model prices used to estimate cost from token counts."""
    quota: UsageQuotaConfig = Field(default_factory=UsageQuotaConfig)
    """How subscription windows are collected per account."""
    insights: UsageInsightsConfig = Field(default_factory=UsageInsightsConfig)
    """Thresholds for the audit's deterministic detectors."""
    commands: UsageCommandsConfig = Field(default_factory=UsageCommandsConfig)
    """How a shell tool call's time is attributed to the command that led it."""


class UIConfig(BaseModel):
    """TUI preferences."""

    model_config = _FROZEN

    theme: str = "auto"
    """`auto`, `dark`, `light`, or the name of a theme file under
    `${user_config_dir}/grove/themes/`.
    """

    keybindings: dict[str, str] = Field(default_factory=dict)
    """Key overrides for the TUI."""


# ─── root model ─────────────────────────────────────────────────────────────


class AgentRoster:
    """Owns how the agent roster is composed: the built-in seed, and the gate on it.

    One owner so the built-in specs exist exactly once — they both seed cascade
    layer 0 (:meth:`seed_layer`) and back ``GroveConfig.agents``'s default. Layer
    0 is what makes the ``agents`` merge-by-name a *refinement* of the built-ins
    instead of a wholesale roster replacement; the direct consequence is that a
    built-in can never be dropped by omission, which is why removing one needs
    the explicit ``builtin_agents`` switch :meth:`allowed` implements.
    """

    # The native entry and its terminal twin ship as a PAIR per provider, so
    # the picker always offers the interactive UI beside the owned session
    # without a config edit; they cascade and merge by name like any built-in.
    BUILTINS: ClassVar[tuple[AgentSpec, ...]] = (
        AgentSpec(
            name="claude",
            command="claude",
            kind="claude_code",
            description="Anthropic Claude Code",
        ),
        AgentSpec(
            name="claude-terminal",
            command="claude",
            kind="claude_code",
            native=False,
            description="Anthropic Claude Code (interactive terminal)",
        ),
        AgentSpec(
            name="codex",
            command="codex",
            kind="codex",
            description="OpenAI Codex CLI",
        ),
        AgentSpec(
            name="codex-terminal",
            command="codex",
            kind="codex",
            native=False,
            description="OpenAI Codex CLI (interactive terminal)",
        ),
        AgentSpec(name="shell", command="$SHELL", description="Plain shell"),
    )

    @classmethod
    def seed_layer(cls) -> dict[str, Any]:
        """The built-ins as cascade layer 0 — raw JSON, not models.

        It merges with the raw file layers through `_deep_merge` / `_merge_agents`
        long before Pydantic sees anything, so it must speak the same dict shape
        the on-disk layers do.
        """
        return {"agents": [spec.model_dump(mode="json") for spec in cls.BUILTINS]}

    @staticmethod
    def entry_name(entry: Any) -> str:
        """The ``name`` of one raw ``agents`` entry, or ``ConfigError``.

        The single definition of "is this a usable agent entry", read by both
        pre-validation passes over raw layers (:meth:`names_in` and
        `_merge_agents`). Both used to skip anything that was not a dict with a
        ``name``, which sounds defensive and is not: the merge runs BEFORE
        ``model_validate``, so a skipped entry never reaches Pydantic and
        ``extra="forbid"`` — the mechanism this module's docstring promises will
        catch every typo — never gets a chance to fire for it. A user adding a
        custom agent and forgetting ``name`` got a roster silently missing it and
        a DEBUG line nobody sees, while mistyping a *field* on the same entry
        raised loudly. Same layer, same file, opposite outcomes.

        Raising here restores one boundary for bad config. The message carries
        the offending entry because, unlike Pydantic's, it cannot name a field
        path — the entry has no valid identity to point at.
        """
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ConfigError(
                f"invalid 'agents' entry {entry!r}: each entry must be an object with a "
                "string 'name' (the key every layer's overrides merge by)"
            )
        return str(entry["name"])

    @classmethod
    def names_in(cls, layers: Iterable[Mapping[str, Any]]) -> frozenset[str]:
        """Every agent ``name`` declared across raw (pre-validation) config layers.

        Reads user JSON before `model_validate` has vetted it, so a malformed
        entry raises here (:meth:`entry_name`) rather than being skipped —
        pre-validation code is the ONLY thing that can report it, since Pydantic
        never sees an entry the merge dropped. A non-list ``agents`` still falls
        through untouched: that one Pydantic does see, and its message points at
        the field far better than anything this pass could say.
        """
        names: set[str] = set()
        for layer in layers:
            entries = layer.get("agents")
            if not isinstance(entries, list):
                continue
            names.update(cls.entry_name(entry) for entry in entries)
        return frozenset(names)

    @staticmethod
    def allowed(specs: Iterable[AgentSpec], *, declared: frozenset[str]) -> list[AgentSpec]:
        """Filter a resolved roster down to what the config layers named.

        The ``builtin_agents: false`` gate. "Declared" alone is the whole rule —
        no built-in-name membership test is needed, because a user-defined agent
        is in ``declared`` by construction, so the only thing this can ever drop
        is a built-in nobody asked for.
        """
        return [spec for spec in specs if spec.name in declared]


class ExclusiveGroups:
    """Owns cross-layer resolution for fields that cannot legally coexist.

    The cascade merges field-by-field, so a lower layer's value survives unless a
    higher layer sets *that same field* — which no layer can do for a mutually
    exclusive pair. A project `.grove/config.json` setting `init_script.path` and
    a machine-local `.grove/config.local.json` setting `init_script.inline` merged
    into a config holding BOTH, and every create in that repo failed and rolled
    back. Each layer was individually valid, so no per-layer validator could see
    it: the merge itself manufactures the invalid combination.

    The rule: the highest-precedence layer that mentions ANY member of a group
    wins the WHOLE group, and every member is stripped from the lower layers
    before `_deep_merge` runs. "Mentions" is key presence, so an explicit
    `"path": null` clears a lower layer's script rather than being a no-op.

    Mechanism only — which fields form a group is declared by the model that owns
    them (`InitScriptConfig.EXCLUSIVE_FIELDS`), never inlined here, and never in
    `_deep_merge`, which stays a policy-free dict merge.
    """

    GROUPS: ClassVar[Mapping[str, tuple[str, ...]]] = {
        "init_script": InitScriptConfig.EXCLUSIVE_FIELDS,
        ContainerConfig.SECTION: ContainerConfig.EXCLUSIVE_FIELDS,
        TicketsConfig.SECTION: TicketsConfig.EXCLUSIVE_FIELDS,
        TelemetryConfig.SECTION: TelemetryConfig.EXCLUSIVE_FIELDS,
    }

    @classmethod
    def resolve(cls, layers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Return `layers` (lowest→highest precedence) with losing members stripped.

        Copies every layer it touches: the inputs come from `_read_json` and
        `AgentRoster.seed_layer()` and belong to the caller. Warns only when a
        strip actually drops something — the silent override is what made the
        original bug invisible, but a no-op group must stay quiet.
        """
        resolved = [dict(layer) for layer in layers]
        for section, fields in cls.GROUPS.items():
            winner = cls._winning_layer(resolved, section, fields)
            if winner is None:
                continue
            kept = [name for name in fields if name in resolved[winner][section]]
            for index in range(winner):
                block = resolved[index].get(section)
                if not isinstance(block, dict):
                    continue
                dropped = [name for name in fields if name in block]
                if not dropped:
                    continue
                resolved[index][section] = {k: v for k, v in block.items() if k not in fields}
                logger.warning(
                    "config cascade: {} set in a higher-precedence layer wins the "
                    "mutually-exclusive group in '{}' — dropping {} from a "
                    "lower-precedence layer",
                    ", ".join(f"'{name}'" for name in kept),
                    section,
                    ", ".join(f"'{name}'" for name in dropped),
                )
        return resolved

    @staticmethod
    def _winning_layer(
        layers: Sequence[Mapping[str, Any]], section: str, fields: tuple[str, ...]
    ) -> int | None:
        """Index of the highest-precedence layer naming any member, else None."""
        for index in reversed(range(len(layers))):
            block = layers[index].get(section)
            if isinstance(block, dict) and any(name in block for name in fields):
                return index
        return None


class CommittedShareFloor:
    """Cross-layer resolution of ``container.agent_config.share``.

    The trust boundary the whole containerization epic rests on: **a committed
    layer may REQUEST a capability; only a non-committed layer may GRANT one.**
    A committed ``.grove/config.json`` travels with the repository, so a repo
    that could raise ``share`` would be granting itself the host's real agent
    credentials — while a repo that *lowers* it (an untrusted vendor drop asking
    to be run isolated) is asking for less, which is always safe to honor.

    Why this can't be a field validator or plain precedence: the cascade merges
    field-by-field with the highest layer winning, so the committed project layer
    legitimately outranks the *user* layer beneath it. Direction, not position,
    is the rule here — the same reason ``ExclusiveGroups`` runs as a pre-pass:
    the merge itself manufactures the value no layer is allowed to author. The
    resolution is therefore a pure pre-pass over the raw layers, run immediately
    before ``_deep_merge``: the committed value is stripped, and re-applied as a
    synthetic TOP layer only when it tightens what everything else resolved to.
    Tightening wins outright — a project forcing ``isolated`` must not be undone
    by a user-level ``full`` — which is exactly the asymmetry precedence alone
    cannot express.
    """

    SECTION: ClassVar[str] = "container"
    SUBSECTION: ClassVar[str] = "agent_config"
    FIELD: ClassVar[str] = "share"

    @classmethod
    def resolve(
        cls, layers: Sequence[Mapping[str, Any]], *, committed: frozenset[int]
    ) -> list[dict[str, Any]]:
        """Return ``layers`` (lowest→highest) with the tighten-only rule applied.

        ``committed`` holds the indexes of layers that ship inside the repo.
        Copies every layer it touches — the inputs belong to the caller.
        """
        resolved = [dict(layer) for layer in layers]
        asks: dict[int, AgentShare] = {}
        for index in committed:
            value = cls._share(resolved[index])
            if value is not None:
                asks[index] = value
        if not asks:
            return resolved
        for index in asks:
            cls._strip(resolved, index)
        granted = cls._share_of(resolved) or "full"
        # Tightest ask across the committed layers: several may declare it, and
        # honoring the loosest would let a nested layer relax a stricter one.
        tightest = min(asks.values(), key=lambda value: SHARE_RANK[value])
        if SHARE_RANK[tightest] >= SHARE_RANK[granted]:
            logger.warning(
                "config cascade: committed 'container.agent_config.share' = '{}' ignored — "
                "a committed layer may only tighten sharing (effective: '{}')",
                tightest,
                granted,
            )
            return resolved
        resolved.append({cls.SECTION: {cls.SUBSECTION: {cls.FIELD: tightest}}})
        return resolved

    @classmethod
    def _share(cls, layer: Mapping[str, Any]) -> AgentShare | None:
        """The share value one raw layer declares, if it declares a legal one."""
        section = layer.get(cls.SECTION)
        if not isinstance(section, dict):
            return None
        block = section.get(cls.SUBSECTION)
        if not isinstance(block, dict):
            return None
        value = block.get(cls.FIELD)
        # An illegal literal is left in place for Pydantic to reject with its own
        # message rather than being silently dropped here.
        return value if value in SHARE_RANK else None

    @classmethod
    def _share_of(cls, layers: Sequence[Mapping[str, Any]]) -> AgentShare | None:
        """The value the remaining (non-committed) layers resolve to."""
        for layer in reversed(layers):
            value = cls._share(layer)
            if value is not None:
                return value
        return None

    @classmethod
    def _strip(cls, layers: list[dict[str, Any]], index: int) -> None:
        """Remove the share key from one layer, leaving its siblings intact."""
        section = dict(layers[index][cls.SECTION])
        block = {k: v for k, v in section[cls.SUBSECTION].items() if k != cls.FIELD}
        section[cls.SUBSECTION] = block
        layers[index][cls.SECTION] = section


class CommittedEnvSource:
    """What a committed layer may say about an :class:`EnvSourceConfig`.

    Same rule as :class:`CommittedShareFloor`, applied to a different capability:
    ``.grove/config.json`` is COMMITTED, so it travels with the repository and is
    untrusted input — **a committed layer may REQUEST a capability, never GRANT
    one**. Direction, not precedence: the committed project layer legitimately
    outranks the user layer beneath it, so only a pre-pass over the raw layers can
    express this.

    ``env_command`` from a committed layer is stripped outright. Honoring it
    would run a repository-supplied command on the host the moment someone clones
    and creates a workspace — remote code execution, dressed as configuration.

    ``env_file`` is deliberately treated DIFFERENTLY, and the asymmetry is the
    interesting part. It executes nothing, so it is not RCE — but it does let a
    committed config name an ARBITRARY HOST PATH and ferry its contents into the
    container (or into a request to a tracker), a real exfiltration primitive that
    a committed ``.devcontainer/`` cannot otherwise reach (the container only ever
    sees the worktree). Banning it outright would cost the legitimate case this
    feature is for — a team committing ``"env_file": ".grove/container.env"`` so
    every clone picks the convention up. So the rule is containment, not denial: a
    committed ``env_file`` must be repo-relative and stay inside the repo
    (`EnvSourceConfig.env_file_is_contained`); absolute paths, ``~``, and any
    ``..`` that escapes are stripped. Non-committed layers (user config,
    ``.grove/config.local.json``, env, CLI) may name anything — the operator is
    trusted by definition.

    An explicit ``null`` from a committed layer is left alone in both cases: it
    clears rather than grants, and asking for LESS is always safe to honor.

    Warnings never include the value of ``env_command`` — a command line can carry
    a token. A rejected ``env_file`` path is named, because that is what makes the
    warning actionable.

    Every :class:`EnvSourceConfig` section is covered by the SAME rule, from the
    one list below: the trust question ("what does this let a committed layer
    REACH") is a property of the mechanism, not of the feature consuming it, so a
    new consumer inherits the boundary instead of re-arguing it.
    """

    SECTIONS: ClassVar[tuple[str, ...]] = (
        ContainerConfig.SECTION,
        TicketsConfig.SECTION,
        TelemetryConfig.SECTION,
    )
    COMMAND_FIELD: ClassVar[str] = "env_command"
    FILE_FIELD: ClassVar[str] = "env_file"

    @classmethod
    def resolve(
        cls, layers: Sequence[Mapping[str, Any]], *, committed: frozenset[int]
    ) -> list[dict[str, Any]]:
        """Return ``layers`` (lowest→highest) with disallowed committed keys gone.

        ``committed`` holds the indexes of layers that ship inside the repository.
        Copies every layer it touches — the inputs belong to the caller.
        """
        resolved = [dict(layer) for layer in layers]
        for index in committed:
            for section in cls.SECTIONS:
                block = resolved[index].get(section)
                if not isinstance(block, dict):
                    continue
                dropped = [
                    name for name in (cls.COMMAND_FIELD, cls.FILE_FIELD) if cls._denied(block, name)
                ]
                if not dropped:
                    continue
                resolved[index][section] = {k: v for k, v in block.items() if k not in dropped}
                for name in dropped:
                    cls._warn(section, name, block[name])
        return resolved

    @classmethod
    def _denied(cls, block: Mapping[str, Any], field: str) -> bool:
        """May a committed layer keep this field's value?

        A missing key or an explicit ``null`` is never denied — neither grants
        anything. Anything that is not a string is left for Pydantic to reject with
        its own message rather than being silently swallowed here.
        """
        value = block.get(field)
        if not isinstance(value, str):
            return False
        if field == cls.COMMAND_FIELD:
            return True
        return not EnvSourceConfig.env_file_is_contained(value)

    @classmethod
    def _warn(cls, section: str, field: str, value: str) -> None:
        """One warning per stripped key, never echoing a command line."""
        if field == cls.COMMAND_FIELD:
            logger.warning(
                "config cascade: committed '{}.env_command' ignored — a committed "
                "layer may not run a host command (set it in your user config or "
                ".grove/config.local.json instead)",
                section,
            )
            return
        logger.warning(
            "config cascade: committed '{}.env_file' = '{}' ignored — a committed "
            "layer may only name a path inside the repository",
            section,
            value,
        )


class DefaultsUserFirst:
    """Resolve the one section where user policy outranks repository policy.

    The ordinary cascade lets a project shape its own workspaces, which is right
    for almost every setting. Create-form defaults are different: the values are
    personal starting answers, so a project suggesting an agent or runtime must
    not replace the operator's own habitual choice. Precedence alone cannot say
    that for one section while preserving it everywhere else, so this is a
    pre-merge pass like :class:`ExclusiveGroups` and the committed-layer guards.

    The rule stays field-level. A user who only saved an agent still receives a
    project's default branch mode; stripping the whole project ``defaults``
    object would turn one personal preference into a refusal of every useful
    team convention.
    """

    SECTION: ClassVar[str] = "defaults"

    @classmethod
    def resolve(
        cls,
        layers: list[dict[str, Any]],
        *,
        user: int | None,
        project: frozenset[int],
    ) -> list[dict[str, Any]]:
        """Strip project keys that the user layer already sets.

        A missing user layer has no preference to protect. A malformed section
        stays untouched so Pydantic owns the useful validation message rather
        than this pre-validation mechanism silently repairing it.
        """
        resolved = [dict(layer) for layer in layers]
        if user is None:
            return resolved
        user_defaults = resolved[user].get(cls.SECTION)
        if not isinstance(user_defaults, dict):
            return resolved
        user_keys = set(user_defaults)
        if not user_keys:
            return resolved
        for index in project:
            project_defaults = resolved[index].get(cls.SECTION)
            if not isinstance(project_defaults, dict):
                continue
            resolved[index][cls.SECTION] = {
                key: value for key, value in project_defaults.items() if key not in user_keys
            }
        return resolved


class EnvReferences:
    """Resolves ``${VAR}`` references in string config values against the env.

    The ``GROVE_<SECTION>__<FIELD>`` layer already lets the environment override
    any field, but the variable NAME is fixed by the schema path. This closes the
    other half: a value may reference a variable of the user's OWN choosing, so a
    config can point at ``${MY_GOTIFY_URL}`` and never hold the value itself. It
    is for non-secret values; a credential still names its variable through the
    dedicated ``*_env`` fields, which this deliberately does not absorb.

    Resolution runs over the MERGED dict, immediately before
    ``GroveConfig.model_validate``, so every field validator sees the resolved
    string and a bad resolved value is rejected exactly like a bad literal.

    **An unset or EMPTY variable is a loud error naming both the variable and the
    dotted config path.** Emptiness reads as absence in the implicit ``GROVE_*``
    override layer, and that rule must not travel here: writing the reference IS
    the opt-in, and the failure it prevents is real — an empty base URL resolves
    to a config that loads fine and then silently sends telemetry nowhere.

    ``$${VAR}`` escapes to a literal ``${VAR}``, and it is the only escape. Text
    that is not this exact shape — a bare ``$``, ``$FOO`` without braces — is
    left completely alone. This is a reference mechanism, not shell
    interpolation.

    ``${repo}`` and ``${repo_name}`` are RESERVED and pass through untouched:
    they belong to :func:`expand_template`, a later expansion against a concrete
    repository, and consuming them here would break every path template using
    them.

    Pure over ``(mapping, env)`` — it never reads ``os.environ`` itself, so the
    caller supplies the environment and a test needs no process patching.
    """

    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\$(\$?)\{([A-Za-z_][A-Za-z0-9_]*)\}")
    RESERVED: ClassVar[frozenset[str]] = frozenset({"repo", "repo_name"})

    @classmethod
    def resolve(cls, merged: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
        """Return ``merged`` with every reference in a string VALUE resolved.

        Keys are never rewritten — a section named after a variable is a section,
        not a reference.
        """
        return {key: cls._value(value, env, key) for key, value in merged.items()}

    @classmethod
    def _value(cls, value: Any, env: Mapping[str, str], path: str) -> Any:
        """Walk one node; only strings are candidates, lists included by index."""
        if isinstance(value, str):
            return cls._text(value, env, path)
        if isinstance(value, dict):
            return {key: cls._value(item, env, f"{path}.{key}") for key, item in value.items()}
        if isinstance(value, list):
            return [cls._value(item, env, f"{path}[{index}]") for index, item in enumerate(value)]
        return value

    @classmethod
    def _text(cls, text: str, env: Mapping[str, str], path: str) -> str:
        """Substitute every reference in one string, or raise naming the first gap."""

        def substitute(match: re.Match[str]) -> str:
            escaped, name = match.group(1), match.group(2)
            if escaped:
                return "${" + name + "}"
            if name in cls.RESERVED:
                return match.group(0)
            value = env.get(name, "")
            if not value:
                state = "is empty" if name in env else "is not set"
                raise ConfigError(
                    f"config: '{path}' references environment variable '{name}', which {state}"
                )
            return value

        return cls.PATTERN.sub(substitute, text)


class DeclaredEnvVars:
    """A field may DECLARE the one environment variable that overrides it.

    The complement of :class:`EnvReferences`, and the two run in opposite
    directions. A reference is written BY the operator in a config file as the
    value ``${VARIABLE}``, names whatever variable that file chose to defer to,
    and is an error when the variable is unset. A declaration is made BY this
    module on a field the operator may have set to a perfectly good literal, is
    always on, and simply does not apply when its variable is unset — nobody
    asked for it, so silence is the only correct answer, and an empty value is
    how a shell says nothing rather than how an operator blanks a setting.
    **Both behaviours are right for their own mechanism; neither is the other's
    inconsistency.**

    Precedence, lowest to highest: the literal in the config file, a ``${VAR}``
    reference written in that file (it IS the file's value, only resolved), the
    field's declared variable, then ``GROVE_<SECTION>__<FIELD>``. The declared
    variable losing to the schema-path layer is deliberate: the schema-path
    name states the exact field it fills and is unambiguous by construction,
    while a declared name is a convenience alias for one field somebody thought
    worth naming. When an operator has exported both, the one that says what it
    means should win.

    So this is a cascade LAYER, not a model validator — a `mode="before"`
    validator on the submodel would run after the whole merge and beat the
    schema-path layer, which is the precedence inverted. The layer is sparse
    (only variables the environment actually supplies), which is what keeps it
    from mentioning fields no one set.

    The name lives in the field's ``json_schema_extra`` so the declaration
    reaches the exported JSON schema and therefore the published configuration
    reference, rather than being a hand-written ``os.environ`` read that no
    reader outside this file can discover. **The schema is the census** — never
    write a second list of these somewhere else.

    Declared only for NON-secret fields. A credential still travels through the
    dedicated ``*_env`` fields, which name a variable the consumer reads at the
    moment of use so no layer ever holds the secret itself.
    """

    SCHEMA_KEY: ClassVar[str] = "x-env-var"

    @classmethod
    def layer(cls, model: type[BaseModel], env: Mapping[str, str]) -> dict[str, Any]:
        """A sparse cascade layer of every declared override ``env`` supplies.

        Walks the model tree so the annotation is the single source of truth.
        Only a field whose annotation IS a submodel is descended into: a list of
        models (``agents``) is not, because an override applies to a field, and
        a list has no field to name.
        """
        layer: dict[str, Any] = {}
        for name, field in model.model_fields.items():
            annotation = field.annotation
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                nested = cls.layer(annotation, env)
                if nested:
                    layer[name] = nested
                continue
            variable = cls.declared(field)
            if variable and env.get(variable):
                layer[name] = env[variable]
        return layer

    @classmethod
    def declared(cls, field: FieldInfo) -> str | None:
        """The variable one field declares, if it declares a usable one."""
        extra = field.json_schema_extra
        variable = extra.get(cls.SCHEMA_KEY) if isinstance(extra, dict) else None
        return variable if isinstance(variable, str) else None


class PanelConfig(BaseModel):
    """One embeddable view served by a service in this workspace's compose stack.

    A panel deliberately names a compose service rather than an origin. A
    committed ``.grove/config.json`` is untrusted input, and arbitrary hosts or
    URLs would turn an iframe convenience into a host-network proxy. Resolution
    therefore proves that this service belongs to the workspace's already-owned
    compose project before the daemon connects. No committed-layer stripping is
    needed: the service and port are contained by that ownership proof.
    """

    model_config = _FROZEN

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    """URL safe identifier, unique within `panels`."""

    title: str = Field(min_length=1, max_length=200)
    """Tab label."""

    service: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    """Docker Compose service name inside this workspace's stack."""

    port: int = Field(ge=1, le=65535)
    """Port the service exposes on its compose network."""

    path: str = "/"
    """Initial path and optional query string for the iframe."""

    @field_validator("path")
    @classmethod
    def _path_is_relative_to_the_panel_origin(cls, value: str) -> str:
        """Refuse a path that could be read as an alternate origin or protocol."""
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("panel path must begin with one '/' and not name an origin")
        return value


class GroveConfig(BaseModel):
    """Merged, validated configuration. Built once per `load_config` call."""

    model_config = _MUTABLE

    schema_url: str = Field(default="", alias="$schema")
    # Repo roots to surface as "known projects" even with zero workspaces, so a
    # freshly-added project (or one whose workspaces were all killed) stays
    # visible in the pickers. Mechanism, not policy: a plain list of path strings
    # (``~`` expanded at consume time, like ``expand_template``); the registry
    # unions these into ``known_roots()`` and drops any that aren't an existing
    # git repo. A user-level concern that still cascades like every other field.
    projects: list[str] = Field(default_factory=list)
    """Repo roots kept visible in every picker even with zero workspaces. `~` expands,
    and a path that is not a git repo is ignored.
    """
    panels: list[PanelConfig] = Field(default_factory=list)
    """Embeddable compose-service views this project permits in each workspace.

    Empty by default. A panel is visible only while the workspace's persisted
    compose project is verified, running, and contains the named service; a
    declaration alone never opens a network destination.
    """
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    agent_cwds: AgentCwdsConfig = Field(default_factory=AgentCwdsConfig)
    defaults: WorkspaceDefaults = Field(default_factory=WorkspaceDefaults)
    builtin_agents: bool = True
    """Whether Grove's own agents are offered alongside the ones you declare. Off makes
    your `agents` list the whole roster.

    ``true`` (the default) is the standing contract: the built-ins are literally layer
    0 of the cascade, so declaring ``agents`` REFINES them and can never hide one
    by omission. That is right for a first-run install and wrong for an operator
    whose fleet runs its own curated profiles — the stock three still show up in
    every picker with no way to say no. Flip this to ``false`` and the roster is
    exactly what your layers declare; name a built-in (even bare
    ``{"name": "claude"}``) to opt it back in and inherit its full built-in spec
    through the same merge-by-name. Forward-compatible by construction: a built-in
    added in a future Grove release stays hidden too, instead of appearing on
    upgrade.

    Hiding an agent removes it from ``find_agent`` as well, so ``create`` rejects
    it on every surface (CLI/MCP/HTTP) and an existing workspace pinned to it can
    no longer resume/respawn — it gets the typed "no longer present in config"
    error. That blast radius is the point: the roster is one list, and hiding an
    agent is the operator saying nothing should run it anymore."""

    agents: list[AgentSpec] = Field(default_factory=lambda: list(AgentRoster.BUILTINS))
    init_script: InitScriptConfig = Field(default_factory=InitScriptConfig)
    tmux: TmuxConfig = Field(default_factory=TmuxConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    tls: TLSConfig = Field(default_factory=TLSConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    lifecycle_max_pending: int = Field(default=64, gt=0)
    """Maximum accepted lifecycle operations, including lock waiters and running work."""
    activity_admission: AdmissionLimits = Field(default_factory=AdmissionLimits)
    """Item and byte reservations for pending and running activity transitions."""
    brief: BriefConfig = Field(default_factory=BriefConfig)
    container: ContainerConfig = Field(default_factory=ContainerConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    permission: PermissionConfig = Field(default_factory=PermissionConfig)
    mewbo: MewboConfig = Field(default_factory=MewboConfig)
    tickets: TicketsConfig = Field(default_factory=TicketsConfig)
    issueops: IssueOpsConfig = Field(default_factory=IssueOpsConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    usage: UsageConfig = Field(default_factory=UsageConfig)

    @model_validator(mode="after")
    def _panel_names_are_unique(self) -> GroveConfig:
        """Reject duplicate URL coordinates before any request can be ambiguous."""
        names = [panel.name for panel in self.panels]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"panel names must be unique: {', '.join(duplicates)}")
        return self

    def find_agent(self, name: str) -> AgentSpec | None:
        for spec in self.agents:
            if spec.name == name:
                return spec
        return None

    # ─── resolved create defaults ────────────────────────────────────────────
    #
    # What a create that says nothing about a field actually gets. Three
    # properties rather than one resolved object, because each is read on its
    # own by a different caller and a bundle would need every caller to know
    # about fields it does not use.
    #
    # These exist so that omitting a field on the wire and displaying the saved
    # answer in a form agree with each other. They did not, and the asymmetry
    # was silent in the worst direction: `WorkspaceDefaultsView` honoured
    # `defaults.runtime` while `RuntimeResolver` read `container.enabled`
    # directly, so a user whose saved default was `host` watched the composer
    # say Host and got a container on every project that had not turned
    # containers off — `container.enabled` defaults to True. The same shape hid
    # `defaults.model` and `defaults.skip_init` from every client that trusted
    # the engine. Resolving HERE means the CLI, TUI, daemon, MCP and issue-ops
    # inherit the user's saved answer for free, which is the argument
    # `agent_cwds.default_path()` already makes one section over.
    #
    # `branch_mode` is deliberately NOT here: `CreateWorkspaceRequest.branch_plan`
    # defaults to `AutoBranch()`, so an omitted plan is indistinguishable from an
    # explicit auto one and there is no "unspecified" for this to answer. That
    # asymmetry is why every client still applies it itself.

    @property
    def default_runtime(self) -> Literal["host", "container"]:
        """Where an unopinionated create runs — the saved answer, else the cascade."""
        return self.defaults.runtime or ("container" if self.container.enabled else "host")

    @property
    def default_brief(self) -> bool:
        """Whether an unopinionated create hands its agent the first-turn brief."""
        return self.brief.enabled if self.defaults.brief is None else self.defaults.brief

    @property
    def default_skip_init(self) -> bool:
        """Whether an unopinionated create skips the init script."""
        return self.defaults.skip_init or False


# ─── public helpers ─────────────────────────────────────────────────────────


def expand_template(template: str, repo_root: Path) -> Path:
    """Replace ${repo}/${repo_name}/~ in a path template against a concrete repo.

    Done at consume time, not validate time, so the same persisted config
    can serve any repo without re-validation.
    """
    expanded = template.replace("${repo}", str(repo_root)).replace("${repo_name}", repo_root.name)
    return Path(expanded).expanduser()


def load_config(
    repo_root: Path | None,
    cli_overrides: dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> GroveConfig:
    """Resolve the full cascade and return a validated GroveConfig.

    Layers (last wins): built-in defaults → user JSON → project JSON →
    project-local JSON → per-field declared env vars (`DeclaredEnvVars`) →
    GROVE_* env vars → caller-supplied CLI overrides.

    Once the layers have merged, any ``${VAR}`` a string value carries is
    resolved against the same environment (`EnvReferences`) — before validation,
    so a resolved value is checked exactly like a literal one.
    """
    env_map = env if env is not None else os.environ
    # Everything the *user* supplied, kept apart from the built-in seed below:
    # `builtin_agents: false` filters the resolved roster down to the names these
    # layers declare, and folding the seed in here would name every built-in and
    # make that gate a no-op.
    overlays: list[dict[str, Any]] = []
    # Which overlays ship INSIDE the repository. Committed layers are untrusted
    # input (they travel with the code), so a capability they may only request —
    # not grant — is resolved by index, not by precedence (`CommittedShareFloor`).
    committed: set[int] = set()
    # `defaults` is intentionally the one inverse-precedence section: preserve
    # the user index and both project indexes for its pre-merge resolver.
    user_index: int | None = None
    project_indexes: set[int] = set()

    user_path = paths.user_config_path()
    if user_path.exists():
        user_index = len(overlays)
        overlays.append(_read_json(user_path))

    if repo_root is not None:
        project_path = paths.project_config_path(repo_root)
        if project_path.exists():
            project_indexes.add(len(overlays))
            committed.add(len(overlays))
            overlays.append(_read_json(project_path))
        local_path = paths.project_local_config_path(repo_root)
        if local_path.exists():
            project_indexes.add(len(overlays))
            overlays.append(_read_json(local_path))

    # Declared per-field variables sit BELOW the schema-path layer: the name a
    # field declares is a convenience alias, the `GROVE_<SECTION>__<FIELD>` name
    # states the exact field it fills (`DeclaredEnvVars`).
    declared_layer = DeclaredEnvVars.layer(GroveConfig, env_map)
    if declared_layer:
        overlays.append(declared_layer)

    env_layer = _parse_env_overrides(env_map)
    if env_layer:
        overlays.append(env_layer)

    if cli_overrides:
        overlays.append(cli_overrides)

    # The built-in agents are literally layer 0 of the cascade, so the `agents`
    # merge-by-name REFINES them instead of replacing the roster: a config that
    # redeclares `claude` keeps kind="claude_code" (the merge contract
    # `_merge_agents` documents), and a scaffolded project config never silently
    # hides codex/shell. Every other field's default already survives via the
    # Pydantic model.
    # Mutually exclusive fields are resolved BEFORE the merge: a field-by-field
    # merge cannot express "my `inline` replaces your `path`", so the two layers
    # would both survive into a combination no layer asked for (`ExclusiveGroups`).
    layers = ExclusiveGroups.resolve([AgentRoster.seed_layer(), *overlays])
    # +1: the seed layer shifts every overlay index by one.
    committed_indexes = frozenset(i + 1 for i in committed)
    layers = CommittedShareFloor.resolve(layers, committed=committed_indexes)
    # Trust runs AFTER exclusivity on purpose, and the ordering has a visible
    # consequence worth stating: the group pass resolves which SOURCE the user
    # chose across layers, then the committed pass strips a choice a committed
    # layer was never allowed to make. So when a committed layer WINS the group
    # with `env_command`, the lower layers' members are already gone and the
    # result is NO env source at all — not a silent fall back to some lower
    # layer's `env_file`. That is the fail-safe outcome: a repo's attempt to
    # claim the slot must not quietly promote the operator's own setting into it.
    layers = CommittedEnvSource.resolve(layers, committed=committed_indexes)
    # +1 again: both user and project overlay indexes are shifted by the seed.
    layers = DefaultsUserFirst.resolve(
        layers,
        user=user_index + 1 if user_index is not None else None,
        project=frozenset(index + 1 for index in project_indexes),
    )
    merged = _deep_merge(*layers)
    # Explicit references resolve LAST, over the merged result rather than per
    # layer: a reference is a property of the value that won, and resolving
    # per layer would pay for values nothing ever reads.
    merged = EnvReferences.resolve(merged, env_map)

    try:
        cfg = GroveConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    # `builtin_agents: false` is filtered AFTER the merge, never by dropping the
    # seed layer — dropping it would re-break this twice over: with no layer
    # declaring `agents` at all, Pydantic's `default_factory` puts the built-ins
    # back anyway, and a config that redeclares `{"name": "claude"}` would lose
    # kind="claude_code" and degrade to `generic`, silently killing that
    # workspace's transcript tracking. Layer 0 must keep merging so refinement
    # works; the roster is trimmed afterwards. Post-validation also means the
    # flag is a real coerced bool (a JSON `"false"` string can't read truthy)
    # and `cfg.agents` are real specs — `_MUTABLE` sets no `validate_assignment`,
    # so this assignment doesn't re-run validators.
    if not cfg.builtin_agents:
        cfg.agents = AgentRoster.allowed(cfg.agents, declared=AgentRoster.names_in(overlays))

    logger.debug("config loaded: {} layers merged", len(layers))
    return cfg


def user_defaults_keys() -> frozenset[str]:
    """Return the raw user-layer defaults fields that outrank project values.

    Provenance disappears when the config layers merge, but the scope picker
    needs this exact fact to explain why a project save cannot affect a field.
    A broken user config belongs to ``load_config``'s loud validation path; the
    advisory UI remains quiet rather than turning a warning into a second error.
    """
    path = paths.user_config_path()
    if not path.exists():
        return frozenset()
    try:
        raw = _read_json(path)
    except ConfigError:
        return frozenset()
    defaults = raw.get(DefaultsUserFirst.SECTION)
    if not isinstance(defaults, dict):
        return frozenset()
    try:
        WorkspaceDefaults.model_validate(defaults)
    except ValidationError:
        return frozenset()
    return frozenset(defaults)


def save_workspace_defaults(
    defaults: WorkspaceDefaults,
    *,
    scope: DefaultsScope,
    repo_root: Path | None = None,
) -> Path:
    """Persist ``defaults`` into ``scope`` and return the path written.

    Reads and rewrites the raw layer rather than a merged :class:`GroveConfig`:
    serializing the latter would bake unrelated cascade values into the target.
    The whole object is replaced so a cleared form field clears its saved value
    instead of leaving an old answer behind. The lock covers the read-modify-write
    because atomic publication alone prevents torn files, not a concurrent save
    from losing an earlier form's update.
    """
    target = scope.path(repo_root)
    with paths.exclusive_lock(target):
        raw = _read_json(target) if target.exists() else {}
        raw[DefaultsUserFirst.SECTION] = defaults.model_dump(exclude_none=True)
        try:
            GroveConfig.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"Invalid configuration: {exc}") from exc
        paths.write_atomic(
            target,
            json.dumps(raw, indent=2) + "\n",
            mode=0o644 if scope is not DefaultsScope.USER else 0o600,
        )
    return target


def add_known_project(repo_root: Path, *, target: Path | None = None) -> bool:
    """Append ``repo_root`` to the user config's ``projects`` list (idempotent).

    Read-modify-write on the raw JSON layer (:func:`_read_json`, the same seam
    :func:`load_config` reads) rather than round-tripping through a merged
    ``GroveConfig`` — a full merge would bake every cascade default back into
    the user file. Targets the *user* config by default: ``projects`` is a
    user-level concern (part of the ``known_roots()`` union), so a repo becomes
    visible cross-project without needing its own committed
    ``.grove/config.json``. Returns ``False`` (no write) when already listed.
    """
    target = target or paths.user_config_path()
    raw = _read_json(target) if target.exists() else {}
    existing = raw.get("projects", [])
    if not isinstance(existing, list):
        raise ConfigError(f"{target}: 'projects' must be a list")
    resolved = str(repo_root.resolve())
    already_known = {str(Path(p).expanduser().resolve()) for p in existing if isinstance(p, str)}
    if resolved in already_known:
        return False
    raw["projects"] = [*existing, resolved]
    try:
        GroveConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc
    paths.ensure_dir(target.parent)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, target)
    return True


def dump_schema_json() -> str:
    """Return the JSON Schema for `GroveConfig` as a `str`.

    Pure helper — no I/O.  Two callers depend on this identical byte stream
    so they cannot drift: `write_schema` (writes the IDE-autocomplete
    file next to the user config) and `grove config schema --stdout`
    (feeds the docs-build hook that renders `configure-reference.md`).
    Trailing newline keeps shell pipelines clean.
    """
    schema = GroveConfig.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def write_schema(target: Path | None = None) -> Path:
    """Write the JSON Schema for GroveConfig next to the user config.

    Users reference it via `"$schema": "./config.schema.json"` for IDE autocomplete.
    Returns the path written to.
    """
    target = target or paths.user_schema_path()
    paths.ensure_dir(target.parent)
    text = dump_schema_json()
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)
    return target


def dump_config_json(cfg: GroveConfig) -> str:
    """Pretty-print a config back to JSON (round-trips through model_validate_json)."""
    return cfg.model_dump_json(indent=2, exclude_none=True, by_alias=True)


# ─── internal: merge + env parsing + I/O ────────────────────────────────────


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Expected JSON object at top level of {path}")
    return data


def _deep_merge(*layers: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge, last-wins. The `agents` list merges by `name`."""
    result: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            existing = result.get(key)
            if key == "agents" and isinstance(value, list) and isinstance(existing, list):
                result[key] = _merge_agents(existing, value)
            elif isinstance(value, dict) and isinstance(existing, dict):
                result[key] = _deep_merge(existing, value)
            else:
                result[key] = value
    return result


def _merge_agents(base: list[Any], overlay: list[Any]) -> list[dict[str, Any]]:
    """Merge agent lists by `name`, **field-level**; new names appended in overlay order.

    An overlay entry whose `name` matches a base entry refines it field-by-field
    (overlay fields win, base fields fill the gaps) rather than replacing it
    wholesale. That is what lets a user tweak only the `claude` agent's `command`
    while keeping its `kind="claude_code"` — without field-merge, omitting `kind`
    on the override would silently drop the agent back to the `generic` default
    and disable Activity Dashboard tracking. This is the cascade principle applied
    at field granularity: each layer overrides the previous, value by value.

    An entry with no usable `name` raises through `AgentRoster.entry_name` — this
    pass is the last place a bad entry can be reported, because dropping it here
    would keep it away from `model_validate` entirely.
    """
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in base:
        name = AgentRoster.entry_name(item)
        if name not in by_name:
            order.append(name)
        by_name[name] = item
    for item in overlay:
        name = AgentRoster.entry_name(item)
        if name not in by_name:
            order.append(name)
            by_name[name] = item
        else:
            by_name[name] = {**by_name[name], **item}
    return [by_name[n] for n in order]


def _parse_env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    """Parse GROVE_<SECTION>__<FIELD>=value into a nested dict.

    Double-underscore separates nesting depth; field names lowercase.
    Values stay strings; Pydantic coerces on validation.

    Only keys whose top-level segment is a real GroveConfig field are
    consumed. GROVE_* is a shared namespace — GROVE_DEBUG, the provider
    token vars config itself names (GROVE_GITEA_TOKEN, ...), the installer
    knobs — and the strict model would otherwise hard-fail every config
    load the moment any of them is exported.

    That carve-out has a cost worth naming: mistyping a *field*
    (`GROVE_TMUX__SESSON_PREFIX`) raises loudly at validation, while mistyping
    the *section* one segment earlier (`GROVE_TMUXX__SESSION_PREFIX`) is
    indistinguishable from a foreign `GROVE_*` var and disappears. The
    exemption still stands — it must, or any exported `GROVE_*` breaks every
    load — but a var carrying the `__` nesting separator is announcing itself
    as a config override, and nothing else in the shared namespace uses that
    shape. So an unknown section WITH a separator warns rather than whispering
    at DEBUG: the one signal available without giving up the carve-out.
    """
    known_fields = GroveConfig.model_fields.keys()
    result: dict[str, Any] = {}
    for key, raw in env.items():
        if not key.startswith("GROVE_"):
            continue
        suffix = key[len("GROVE_") :]
        if not suffix:
            continue
        parts = [p.lower() for p in suffix.split("__") if p]
        if not parts:
            continue
        if parts[0] not in known_fields:
            if len(parts) > 1:
                logger.warning(
                    "ignoring env var {}: {!r} is not a config section — a GROVE_*__* "
                    "variable that names no section is silently dropped, so this is "
                    "almost certainly a typo",
                    key,
                    parts[0],
                )
            else:
                logger.debug("ignoring non-config env var {}", key)
            continue
        cursor: dict[str, Any] = result
        for part in parts[:-1]:
            nxt = cursor.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[parts[-1]] = raw
    return result
