"""The ``@devcontainers/cli`` boundary: read-configuration / build / up / exec.

Grove writes **no** devcontainer.json parser. The spec (containers.dev) and its
MIT reference implementation are adopted verbatim and invoked as a subprocess —
feature resolution, OCI fetch and metadata merge are a multi-thousand-line
liability with no upside. What Grove owns lives here: the argv/JSON boundary
(:class:`DevcontainerCli`), the Pydantic shapes the CLI's JSON is parsed into so
no raw dict escapes, the pure overlay synthesis (:class:`GroveOverlay`), and the
packaged default config (:class:`DefaultDevcontainerConfig`).

Sibling of ``container.py`` in posture: argv-only, ``shell=False``, one narrow
error type (:class:`~grove.core.errors.DevcontainerError`), a structured log per
outcome. The pure parts — fingerprinting, overlay synthesis, custom-mount
detection — are separate and unit-testable with no Docker and no network.

Two hard-won constraints are encoded here rather than left to call sites:

* ``--override-config`` **replaces**, it does not merge. Grove must hand the CLI
  a *complete* config (the project's own merged configuration plus the overlay)
  or the project's features, mounts and hooks are silently dropped. The file is
  written **inside the worktree** (``.devcontainer/.grove-override.json``)
  because ``build.dockerfile``, ``dockerComposeFile`` and local features resolve
  relative to the config file's own directory.
* A linked worktree's ``.git`` is a *file* pointing at
  ``<main>/.git/worktrees/<name>``, so mounting only the worktree breaks git
  inside the container. ``--mount-git-worktree-common-dir`` exists but is
  silently ignored when the config defines a custom ``workspaceMount``
  (devcontainers/cli#1243) — precisely for teams sophisticated enough to have
  tuned their config, and ``up`` still succeeds while git breaks confusingly.
  Grove therefore synthesizes the common-dir bind mount itself, at the SAME
  absolute path inside the container, and never relies on that flag.

Dependencies flow inward: this module imports ``config``/``errors``/``git``; it
is imported by the launch/lifecycle layers, never the reverse.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from grove.core import paths
from grove.core.config import ContainerConfig
from grove.core.errors import DevcontainerError
from grove.core.git import GitRepo

_NO_LABELS: Mapping[str, str] = {}
_NO_ENV: Mapping[str, str] = {}

#: Where the complete generated config is written, relative to the worktree.
OVERRIDE_CONFIG_RELPATH = Path(".devcontainer") / ".grove-override.json"

_WIRE = ConfigDict(populate_by_name=True, extra="allow")
"""Config shapes round-trip UNKNOWN keys (``extra="allow"``).

A devcontainer.json is open-ended and Grove re-emits what it reads: anything
dropped here would be silently dropped from the override config, which is the
exact failure ``--override-config``-replaces-not-merges makes catastrophic. Only
the properties Grove reasons about are declared; the rest ride through.
"""

_RESULT = ConfigDict(populate_by_name=True, extra="ignore")
"""CLI *result* envelopes are read, never re-emitted — extras are noise."""


class DevcontainerMount(BaseModel):
    """One entry of ``mounts`` in object form (the CLI also accepts strings)."""

    model_config = _WIRE

    type: str = "bind"
    source: str = ""
    target: str = ""

    @classmethod
    def bind(cls, source: Path, target: Path) -> DevcontainerMount:
        """A bind mount of a host path at an absolute container path."""
        return cls(type="bind", source=str(source), target=str(target))

    def to_flag(self) -> str:
        """The ``--mount`` flag's comma-separated string form of this mount."""
        return f"type={self.type},source={self.source},target={self.target}"


class DevcontainerConfig(BaseModel):
    """A devcontainer configuration — the project's, or the complete overlay.

    Only the properties Grove reasons about are typed; everything else survives
    via ``extra="allow"`` so a generated override config is a superset of what
    was read, never a lossy subset.

    **``mergedConfiguration`` is a DIFFERENT SCHEMA from devcontainer.json, and
    Grove read it as if it were the same one.** This class parses both — the
    ``configuration`` envelope (a devcontainer.json verbatim) and the
    ``mergedConfiguration`` one (that config plus every Feature's contributed
    metadata) — then re-emits what it read as the ``--override-config`` file,
    which the CLI parses as a devcontainer.json again. That round trip is
    lossless only where the two schemas agree, and they do not. The differences
    are enumerated here exhaustively, from a real ``read-configuration`` diffed
    against ``@devcontainers/cli`` 0.88.0 (the payload is the fixture in
    ``tests/core/test_devcontainer.py``):

    * **A — RENAMED.** The five lifecycle hooks become plural arrays carrying
      every Feature's command and the project's own together:
      ``postCreateCommand`` → ``postCreateCommands``, likewise on-create,
      update-content, post-start and post-attach. Remedy: **translate** on the
      way out (:meth:`with_singular_lifecycle_hooks`). ``extra="allow"`` carried
      the plural names faithfully into the override file — the value was never
      dropped — but that file's reader knows only the singular names and ignores
      them, so a project's entire setup would be present-but-inert. Emitting
      both names would give the reader two answers for one hook, so the plural
      is removed as the singular is written.
    * **B — RESHAPED.** Every ``customizations`` namespace becomes a LIST of
      per-layer objects (``{"grove": {…}}`` → ``{"grove": [{…}]}``). Remedy:
      **tolerate** on the way in (:attr:`requires_container`), never translate.
      Flattening means re-implementing the CLI's own per-tool merge — extensions
      concatenate, settings deep-merge — the multi-thousand-line liability this
      module exists to not own, and getting it wrong would corrupt a project's
      editor config rather than merely lose it.
    * **C — MATERIALIZED DEFAULTS.** ``init`` and ``privileged`` come back as
      ``false`` even when no layer mentions them (``portsAttributes`` /
      ``remoteEnv`` / ``containerEnv`` likewise as ``{}``), which makes "the
      project pinned it" indistinguishable from "the CLI defaulted it" — and
      breaks every overlay rule that yields to an explicit project value.
      :meth:`GroveOverlay.apply`'s ``init`` default is exactly such a rule, and
      it therefore never applies. **Known live bug, deliberately not fixed
      here:** the fix needs the RAW envelope as the witness of what the project
      actually wrote, which is neither of the remedies above.

    Judge the next difference by which of those it is: **translate** when what
    is lost is a NAME the downstream reader does not know, **tolerate** when it
    is a SHAPE whose reconstruction would mean re-deriving the CLI's own merge.
    """

    model_config = _WIRE

    name: str | None = None
    image: str | None = None
    workspace_mount: str | None = Field(default=None, alias="workspaceMount")
    workspace_folder: str | None = Field(default=None, alias="workspaceFolder")
    customizations: dict[str, Any] = Field(default_factory=dict)
    mounts: list[str | DevcontainerMount] = Field(default_factory=list)
    remote_env: dict[str, str] = Field(default_factory=dict, alias="remoteEnv")
    run_args: list[str] = Field(default_factory=list, alias="runArgs")
    init: bool | None = None
    security_opt: list[str] = Field(default_factory=list, alias="securityOpt")
    cap_add: list[str] = Field(default_factory=list, alias="capAdd")
    shutdown_action: str | None = Field(default=None, alias="shutdownAction")
    post_start_command: Any = Field(default=None, alias="postStartCommand")
    """Runs on EVERY container start, which is why Grove's egress firewall rides
    it — a resume must re-apply the policy, and `up` re-runs this hook.

    Deliberately untyped: the spec allows a string, an argv list, OR an object of
    named parallel commands, and Grove only ever *prepends* to it (see
    ``GroveOverlay.post_start_command``). Narrowing it would drop the shapes it
    does not model, which for an override config that REPLACES the project's own
    is exactly the lossy failure ``extra="allow"`` exists to prevent."""

    #: Difference **A** (see the class docstring): merged → spec hook names.
    LIFECYCLE_HOOKS: ClassVar[Mapping[str, str]] = {
        "onCreateCommands": "onCreateCommand",
        "updateContentCommands": "updateContentCommand",
        "postCreateCommands": "postCreateCommand",
        "postStartCommands": "postStartCommand",
        "postAttachCommands": "postAttachCommand",
    }

    #: Difference **C**: keys the merged envelope materializes, → the CLI's own
    #: default for each. Membership is not "the merged form invents this key" —
    #: ``portsAttributes`` / ``remoteEnv`` / ``containerEnv`` are invented too
    #: and deliberately absent here. It is "a value that could be MISTAKEN for a
    #: decision someone made": an empty collection merges as identity, so no
    #: consumer can read intent into it, while a materialized scalar is
    #: indistinguishable from a pin. Add a key here when Grove grows a rule that
    #: yields to an explicit project value for it — ``privileged`` is listed
    #: ahead of any such rule precisely so the trap cannot be sprung again.
    MATERIALIZED_DEFAULTS: ClassVar[Mapping[str, Any]] = {"init": False, "privileged": False}

    @staticmethod
    def shell_form(command: Any) -> str:
        """One spec-shaped lifecycle command rendered as a single shell string.

        The spec allows three shapes per command and each collapses differently:
        an argv list is quoted (never re-split), and an object of named parallel
        commands becomes a sequential chain — the singular form has no way to
        say "a parallel group inside a sequence", and running them in a
        deterministic order is the only rendering that keeps every command.
        """
        if isinstance(command, dict):
            return " && ".join(DevcontainerConfig.shell_form(v) for v in command.values())
        if isinstance(command, list):
            return shlex.join(str(token) for token in command)
        return str(command)

    @classmethod
    def _flatten_hook(cls, commands: list[Any]) -> Any:
        """Collapse ONE merged hook array to the singular property's value.

        A single command rides through **verbatim**, shape and all — that is the
        no-Features case (and every object hook in practice), and re-rendering
        it would be a lossy no-op. Several are joined with ``&&`` rather than
        concatenated: the CLI runs the array in order and stops on a failure, so
        the chain reproduces its semantics, and Grove's own egress hook then
        prepends to the whole sequence the way ``_compose_post_start`` already
        documents. Feature commands come first in the CLI's own ordering; it is
        preserved rather than re-derived.
        """
        if len(commands) == 1:
            return commands[0]
        return " && ".join(cls.shell_form(command) for command in commands)

    def without_unauthored_defaults(self, *, witness: DevcontainerConfig) -> DevcontainerConfig:
        """This config with every CLI-materialized default dropped back to unset.

        Difference **C** in the class docstring, undone — and the only one that
        needs a second object, because the merged envelope has destroyed the
        fact in question: whether any layer said anything at all. *witness* is
        the RAW ``configuration``, which records exactly what the project wrote.

        **Absence from the witness is not enough on its own**, and that is the
        subtle half: a Feature can contribute ``init: true`` without the project
        naming it, so "the project did not write this" and "nobody asked for
        this" are different statements. A key is dropped only when the merged
        value *also* equals the CLI's own default — a non-default value proves
        some layer asked for it, whoever that layer was.

        The distinction matters exactly once per key, at the moment an overlay
        decides whether it is looking at a default it may replace or a decision
        it must respect (:meth:`GroveOverlay.apply`'s ``init``). Returns ``self``
        when nothing was materialized, so the raw arm — which passes itself as
        its own witness — is provably a no-op.
        """
        payload = self.model_dump(by_alias=True)
        # Key PRESENCE, not truthiness: an explicit `false` is a decision, and
        # `exclude_unset` is what separates "the layer wrote this" from "the
        # model defaulted it". Same rule `ExclusiveGroups` reads config layers by.
        authored = witness.model_dump(by_alias=True, exclude_unset=True)
        dropped = [
            key
            for key, default in self.MATERIALIZED_DEFAULTS.items()
            if key not in authored and key in payload and payload[key] == default
        ]
        if not dropped:
            return self
        for key in dropped:
            del payload[key]
        return DevcontainerConfig.model_validate(payload)

    def with_singular_lifecycle_hooks(self) -> DevcontainerConfig:
        """This config with every merged hook array folded onto its spec name.

        Difference **A** in the class docstring, undone. Returns ``self``
        untouched when there is nothing to fold, so a project with no Features —
        whose config already speaks the singular form — is provably unaffected.
        The plural key is REMOVED as the singular one is written: an override
        file carrying both would offer its reader two answers for one hook, and
        only one of them is the name that reader looks for.
        """
        payload = self.model_dump(by_alias=True)
        folded = {key: payload.pop(key) for key in self.LIFECYCLE_HOOKS if key in payload}
        if not folded:
            return self
        for plural, singular in self.LIFECYCLE_HOOKS.items():
            raw = folded.get(plural)
            if raw is None:
                continue
            commands = raw if isinstance(raw, list) else [raw]
            if not commands:
                continue
            payload[singular] = self._flatten_hook(commands)
        return DevcontainerConfig.model_validate(payload)

    @property
    def requires_container(self) -> bool:
        """Whether ``customizations.grove.requires_container`` raises the floor.

        A committed layer may *require* the container runtime — a capability
        request, which is always allowed — so a ``--runtime host`` create in
        that repo is refused rather than silently downgraded. ANY layer asking
        is enough, which is the same direction the floor already has.

        Both shapes are read because this property is asked of a config parsed
        from either envelope (see :attr:`MERGED_ENVELOPE`, difference **B**):
        ``devcontainer.json`` writes one object per namespace, the merged form
        writes a LIST of them, one per contributing layer. Reading only the
        object shape answers "not required" for every real project if the
        caller reads the merged form, so the floor must check both.

        Defensive throughout: a value of any other type, or a layer that is not
        an object, is simply "not required" and never a parse error — this file
        is untrusted input, and a broken config must not block a host create.
        """
        grove = self.customizations.get("grove")
        layers = grove if isinstance(grove, list) else [grove]
        return any(isinstance(layer, dict) and layer.get("requires_container") for layer in layers)

    @property
    def has_custom_workspace_mount(self) -> bool:
        """Whether the config pins its own ``workspaceMount``.

        The trigger for devcontainers/cli#1243: with a custom mount the CLI
        silently ignores ``--mount-git-worktree-common-dir``. Grove synthesizes
        the mount unconditionally, so this is a *diagnostic* (and the reason the
        flag is never passed), not a branch in the synthesis.
        """
        return bool(self.workspace_mount)

    def to_json(self) -> str:
        """Serialize back to devcontainer.json text (aliases, extras, no nulls)."""
        payload = self.model_dump(by_alias=True, exclude_none=True, mode="json")
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    def fingerprint(self) -> str:
        """Deterministic short hash of the COMPLETE config.

        The identity a prebuilt image is tagged and cached by: two worktrees
        whose resolved configs are byte-equal share an image, and any edit — a
        feature option, a mount, a hook — mints a new tag. Canonical (sorted
        keys) so dict ordering can never fork the cache.
        """
        payload = self.model_dump(by_alias=True, exclude_none=True, mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def write_override(self, worktree: Path) -> Path:
        """Write this complete config to ``$WT/.devcontainer/.grove-override.json``.

        Inside the worktree on purpose — ``build.dockerfile``,
        ``dockerComposeFile`` and local features resolve relative to the config
        file's own directory, so an override parked in a Grove state dir breaks
        every relative path in a real project.
        """
        target = worktree / OVERRIDE_CONFIG_RELPATH
        paths.ensure_dir(target.parent)
        target.write_text(self.to_json(), encoding="utf-8")
        logger.debug("devcontainer: wrote override config {}", target)
        return target


class DevcontainerWorkspace(BaseModel):
    """The ``workspace`` envelope of ``read-configuration``."""

    model_config = _RESULT

    workspace_folder: str | None = Field(default=None, alias="workspaceFolder")


class ReadConfigurationResult(BaseModel):
    """Parsed ``devcontainer read-configuration --include-merged-configuration``.

    Pure and Docker-free: the CLI resolves features and merges image metadata
    without touching an engine, which is what makes this the seam Grove reads
    before deciding anything about a container.
    """

    model_config = _RESULT

    configuration: DevcontainerConfig = Field(default_factory=DevcontainerConfig)
    merged_configuration: DevcontainerConfig | None = Field(
        default=None, alias="mergedConfiguration"
    )
    workspace: DevcontainerWorkspace | None = None

    @property
    def effective(self) -> DevcontainerConfig:
        """The config to overlay onto: merged when present, else the raw one.

        Always prefer the merged form — it is the project's config *plus* the
        metadata contributed by its features, and an overlay built on the raw
        config would drop exactly that.

        Normalized on the way out, because the merged form names the lifecycle
        hooks differently from the file Grove writes them back into — the
        override config's reader would carry them through and ignore them
        (difference **A** in :class:`DevcontainerConfig`) — and because it
        materializes defaults no layer authored, which reads to an overlay as a
        decision the project never made (difference **C**). Both folds are
        no-ops on the raw arm, which is its own witness, so ``effective`` speaks
        the spec's own vocabulary no matter which envelope it came from.

        This is the ONE place both envelopes are in scope, which is why the
        witness lives here rather than being threaded into every consumer: a
        caller asks one object what the configuration effectively is, and never
        has to correlate two to find out what the project actually wrote.
        """
        base = self.merged_configuration or self.configuration
        return base.without_unauthored_defaults(
            witness=self.configuration
        ).with_singular_lifecycle_hooks()


class BuildResult(BaseModel):
    """Parsed ``devcontainer build --log-format json`` final stdout object."""

    model_config = _RESULT

    outcome: Literal["success", "error"] = "error"
    image_name: list[str] = Field(default_factory=list, alias="imageName")


class UpResult(BaseModel):
    """Parsed ``devcontainer up --log-format json`` final stdout object.

    The error arm matters as much as the success arm: an ``outcome:"error"``
    can still carry a ``containerId``, and that container exists on the host —
    swallowing it leaks a container teardown (story 4) can never find.
    """

    model_config = _RESULT

    outcome: Literal["success", "error"] = "error"
    container_id: str | None = Field(default=None, alias="containerId")
    remote_user: str | None = Field(default=None, alias="remoteUser")
    remote_workspace_folder: str | None = Field(default=None, alias="remoteWorkspaceFolder")
    compose_project_name: str | None = Field(default=None, alias="composeProjectName")
    message: str | None = None
    description: str | None = None

    @property
    def failure_detail(self) -> str:
        """Human-readable reason, for the raised error's message."""
        return self.message or self.description or "devcontainer up reported outcome=error"


class ProgressEvent(BaseModel):
    """One line-delimited JSON progress record off the CLI's **stderr**.

    Exposed through an ``on_progress`` callback so a caller can forward it (the
    daemon's SSE stream is story-later wiring; this module only provides the
    seam). A line that is not JSON is never dropped — it becomes a ``raw``
    event, because a build's most useful output is often the unstructured tail.
    """

    model_config = _RESULT

    type: str = ""
    level: int | None = None
    timestamp: int | None = None
    text: str = ""

    @classmethod
    def parse(cls, line: str) -> ProgressEvent:
        """Parse one stderr line; non-JSON becomes ``type="raw"``."""
        stripped = line.strip()
        try:
            payload = json.loads(stripped)
        except ValueError:
            return cls(type="raw", text=stripped)
        if not isinstance(payload, dict):
            return cls(type="raw", text=stripped)
        return cls.model_validate(payload)


ProgressCallback = Callable[[ProgressEvent], None]


class GroveOverlay(BaseModel):
    """Grove's additive overlay over a project's complete devcontainer config.

    Pure by construction: :meth:`apply` takes the already-read config and
    returns a new complete one — no subprocess, no filesystem. Every field
    defaults to "add nothing", so the *policy* (which isolation knobs a
    workspace gets) cascades from ``container.*`` config at the call site rather
    than being baked in here.

    Only properties with **no** CLI flag belong here — ``runArgs``, ``init``,
    ``securityOpt``, ``capAdd``, ``shutdownAction``. Anything with a direct flag
    (``--mount``, ``--remote-env``, ``--secrets-file``, ``--additional-features``,
    ``--cache-from``) is passed as a flag instead, keeping the generated file as
    close to the project's own config as possible.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_args: tuple[str, ...] = ()
    init: bool | None = None
    """A DEFAULT for the container's PID 1 reaper — the project still outranks it.

    Unlike every other field here, :meth:`apply` yields to a project that set
    ``init`` itself; see the comment there for the systemd case that makes an
    explicit ``false`` legitimate."""
    security_opt: tuple[str, ...] = ()
    cap_add: tuple[str, ...] = ()
    shutdown_action: str | None = None
    mounts: tuple[str | DevcontainerMount, ...] = ()
    """Extra mounts, as models OR as already-rendered docker mount strings.

    The string form is load-bearing, not a convenience: a `readonly` bind can
    only be expressed here. `devcontainer up --mount` accepts strictly
    `type=<bind|volume>,source=,target=[,external=]` and rejects the whole
    invocation on anything else, so a read-only mount CANNOT go through the
    flag — it has to ride the configuration's own `mounts` array, which is
    passed through to docker verbatim."""
    remote_env: dict[str, str] = Field(default_factory=dict)
    git_author_name: str = ""
    git_author_email: str = ""
    post_start_command: str = ""
    """A command to run FIRST on every container start (Grove's egress firewall).

    Composed with the project's own ``postStartCommand``, never replacing it: a
    project's hook starts its services, and silently dropping it would break the
    workspace in a way that looks like the project's own bug. Grove's runs first
    so the boundary is up before anything the project starts can reach the
    network."""

    #: ``safe.directory`` must be set or every in-container git command fails on
    #: dubious ownership (the mounted worktree is owned by the host uid).
    SAFE_DIRECTORY_ENV: ClassVar[Mapping[str, str]] = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": "*",
    }

    @staticmethod
    def worktree_common_dir(worktree: Path) -> Path | None:
        """The repo's shared git dir when *worktree* is a LINKED worktree, else None.

        The side effect is delegated to ``git.py`` (the sanctioned surface); the
        decision — "is a second mount needed at all" — is this one comparison. A
        plain checkout's common dir IS its own ``.git``, so nothing extra is
        mounted and the ordinary case stays untouched.
        """
        common = GitRepo(worktree).common_dir()
        if common is None or common == (worktree / ".git").resolve():
            return None
        return common

    def git_env(self, *, common_dir: Path | None) -> dict[str, str]:
        """The curated git environment forwarded into the container.

        Deliberately env-only: the host ``~/.gitconfig`` is never mounted (it
        carries credentials, signing keys and machine-local paths), and
        ``GIT_CONFIG_COUNT``/``_KEY_n``/``_VALUE_n`` expresses the one setting
        that must hold without writing a file the project can see. Identity is
        forwarded only when the caller supplied it — a blank name/email is left
        to the image's own configuration rather than exported as empty.
        """
        env = dict(self.SAFE_DIRECTORY_ENV)
        if self.git_author_name:
            env["GIT_AUTHOR_NAME"] = self.git_author_name
            env["GIT_COMMITTER_NAME"] = self.git_author_name
        if self.git_author_email:
            env["GIT_AUTHOR_EMAIL"] = self.git_author_email
            env["GIT_COMMITTER_EMAIL"] = self.git_author_email
        if common_dir is not None:
            # The worktree's `.git` file points here by ABSOLUTE path, so the
            # mount target must match it exactly for git to resolve inside.
            env["GIT_COMMON_DIR"] = str(common_dir)
        return env

    def apply(
        self,
        base: DevcontainerConfig,
        *,
        git_common_dir: Path | None = None,
    ) -> DevcontainerConfig:
        """Return a COMPLETE config: *base* plus this overlay, nothing dropped.

        Additive everywhere it can be (mounts, runArgs, securityOpt, capAdd,
        remoteEnv), replacing only the scalar knobs the overlay actually sets.
        Grove's ``remoteEnv`` keys win a collision with the project's — they are
        the isolation contract, not a preference.

        *git_common_dir* (from :meth:`worktree_common_dir`) is bound at the SAME
        absolute path inside the container; without it a linked worktree's git
        is broken no matter what the CLI's own flag claims (cli#1243).
        """
        merged = base.model_copy(deep=True)
        merged.run_args = [*merged.run_args, *self.run_args]
        merged.security_opt = [*merged.security_opt, *self.security_opt]
        merged.cap_add = [*merged.cap_add, *self.cap_add]
        if self.init is not None and merged.init is None:
            # `merged.init is None` is only a trustworthy reading of "the
            # project said nothing" because `ReadConfigurationResult.effective`
            # un-materializes the CLI's own defaults first. Read straight
            # off `mergedConfiguration`, this test can never be true — the merged
            # envelope reports `init: false` whether or not any layer asked, so
            # this default silently yielded to a decision nobody made, and no
            # container ever got the reaper. If a caller ever hands `apply` a
            # config from somewhere other than `effective`, that guarantee is the
            # one it has to re-establish.
            #
            # `init` is the ONE scalar the overlay supplies as a DEFAULT rather
            # than a decision: a project that says nothing gets a reaper, a
            # project that says `false` keeps its own answer. That opt-out is
            # real, not theoretical — an image running systemd checks
            # `getpid() == 1`, and putting docker-init in front of it makes PID 1
            # tini and breaks the container outright. Reaping is a courtesy Grove
            # extends to the unspecified case; it is not part of the isolation
            # contract the way egress and the restart policy are, so it does not
            # get to override an explicit project value.
            merged.init = self.init
        if self.shutdown_action is not None:
            merged.shutdown_action = self.shutdown_action
        mounts: list[str | DevcontainerMount] = [*merged.mounts, *self.mounts]
        if git_common_dir is not None:
            mounts.append(DevcontainerMount.bind(git_common_dir, git_common_dir))
        merged.mounts = mounts
        merged.remote_env = {
            **merged.remote_env,
            **self.git_env(common_dir=git_common_dir),
            **self.remote_env,
        }
        merged.post_start_command = self._compose_post_start(merged.post_start_command)
        return merged

    def _compose_post_start(self, existing: Any) -> Any:
        """Grove's start hook, prepended to whatever the project already declared.

        The three spec shapes are handled by what each can safely absorb: an
        object of named parallel commands gains Grove's under its own key (they
        run in parallel, so ordering is not expressible there anyway); a string
        or argv list is joined with ``&&`` so the project's hook runs only once
        the firewall is up — and, because ``set -e`` semantics are what the user
        expects from a failed boundary, a failing firewall stops the rest.

        *existing* is whatever the effective config resolved to, which for a
        project with Features is the ``&&`` chain of every contributed hook
        (:meth:`DevcontainerConfig._flatten_hook`) — so the firewall precedes
        the whole sequence, not just its first command.
        """
        if not self.post_start_command:
            return existing
        if existing is None or existing == "":
            return self.post_start_command
        if isinstance(existing, dict):
            return {"grove": self.post_start_command, **existing}
        if isinstance(existing, list):
            existing = DevcontainerConfig.shell_form(existing)
        return f"{self.post_start_command} && {existing}"


class DefaultDevcontainerConfig:
    """The packaged L0 config used when a repo has no ``.devcontainer/``.

    Shipped in the wheel as a data file and handed to the CLI via ``--config``,
    so a repo with no devcontainer of its own is still containerized. It MUST
    stay self-contained — ``image`` + ``features`` only, no ``build.dockerfile``,
    no ``dockerComposeFile``, no local-path features — because a config living
    outside the worktree cannot resolve config-directory-relative paths.

    ``container.default_config`` swaps it wholesale (mechanism, not policy):
    publishing an official Grove base image later is a config change, not new
    machinery here.
    """

    RESOURCE: ClassVar[str] = "default-devcontainer.json"

    @classmethod
    def path(cls, cfg: ContainerConfig | None = None) -> Path:
        """The config file to pass as ``--config``: the override, else packaged."""
        override = (cfg.default_config if cfg else "").strip()
        if override:
            return Path(override).expanduser()
        # Anchored on the package, not `__file__`, so the data file is resolved
        # the same way in an editable checkout and an installed wheel.
        return Path(str(resources.files("grove.core").joinpath("data", cls.RESOURCE)))

    @classmethod
    def load(cls, cfg: ContainerConfig | None = None) -> DevcontainerConfig:
        """Read + validate the default config into the boundary model."""
        target = cls.path(cfg)
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise DevcontainerError(
                f"cannot read default devcontainer config {target}: {exc}"
            ) from exc
        try:
            return DevcontainerConfig.model_validate_json(raw)
        except ValueError as exc:
            raise DevcontainerError(f"invalid default devcontainer config {target}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class DevcontainerProbe:
    """Availability of the CLI on this host — the precondition, not a provision.

    Grove never vendors or installs ``@devcontainers/cli`` (the official
    installer bundles its own Node, so there is no host-Node dependency to
    manage). A later story renders this; the check lives with the boundary that
    knows the binary name.
    """

    binary: str
    available: bool
    version: str = ""

    INSTALL_HINT: ClassVar[str] = (
        "install the devcontainer CLI: `npm install -g @devcontainers/cli` "
        "(or VS Code → Dev Containers: Install devcontainer CLI)"
    )

    @property
    def detail(self) -> str:
        """One line fit to print: the version, or an actionable install hint."""
        if self.available:
            return f"{self.binary} {self.version}".strip()
        return f"{self.binary} not found — {self.INSTALL_HINT}"


class DevcontainerCli:
    """The ``@devcontainers/cli`` subprocess boundary — all four create-path verbs.

    One cohesive class holding the resolved binary and emitting argv through
    methods; every invocation is a list-argv ``subprocess`` call with
    ``shell=False``. Failures narrow to :class:`DevcontainerError` so the
    lifecycle fork handles exactly one type, mirroring ``ContainerDriver``.

    Teardown is absent on purpose: the CLI has no ``stop``/``down``/``list``
    verb, so tearing a container down goes through ``container.py``'s Docker
    driver (a separate story) using the id labels ``up`` was given.
    """

    def __init__(self, *, binary: str = "devcontainer", timeout: float | None = None) -> None:
        self._binary = binary
        self._timeout = timeout

    @property
    def binary(self) -> str:
        return self._binary

    # ─── probes ────────────────────────────────────────────────────────────

    def probe(self) -> DevcontainerProbe:
        """Best-effort presence + version read — never raises.

        A precondition check must be able to report "absent" without being an
        error itself; the loud failure belongs to the verb that then can't run.
        """
        try:
            result = subprocess.run(
                [self._binary, "--version"],
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=30,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("devcontainer probe failed: {}", exc)
            return DevcontainerProbe(binary=self._binary, available=False)
        if result.returncode != 0:
            return DevcontainerProbe(binary=self._binary, available=False)
        return DevcontainerProbe(binary=self._binary, available=True, version=result.stdout.strip())

    # ─── the four create-path verbs ────────────────────────────────────────

    def read_configuration(
        self,
        workspace_folder: Path,
        *,
        config: Path | None = None,
        include_merged: bool = True,
    ) -> ReadConfigurationResult:
        """Resolve the project's configuration — pure, no Docker engine needed.

        ``include_merged`` is on by default because the merged form (config plus
        every feature's contributed metadata) is the only honest input to
        overlay synthesis.
        """
        argv = ["read-configuration", "--workspace-folder", str(workspace_folder)]
        if include_merged:
            argv.append("--include-merged-configuration")
        if config is not None:
            argv += ["--config", str(config)]
        result = self._run(argv, action="read devcontainer configuration", cwd=workspace_folder)
        if result.returncode != 0:
            raise DevcontainerError(
                f"read-configuration failed for {workspace_folder}: {self._tail(result.stderr)}"
            )
        return self._parse(result.stdout, ReadConfigurationResult, action="read-configuration")

    def build(
        self,
        workspace_folder: Path,
        *,
        image_name: str = "",
        cache_from: Sequence[str] = (),
        config: Path | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> BuildResult:
        """Build the image for a config without starting a container."""
        argv = ["build", "--workspace-folder", str(workspace_folder)]
        if image_name:
            argv += ["--image-name", image_name]
        for ref in cache_from:
            argv += ["--cache-from", ref]
        if config is not None:
            argv += ["--config", str(config)]
        stdout, stderr, code = self._stream(
            argv, on_progress=on_progress, action="build", cwd=workspace_folder
        )
        if code != 0:
            raise DevcontainerError(f"devcontainer build failed: {self._tail(stderr)}")
        return self._parse(stdout, BuildResult, action="build")

    def up(
        self,
        workspace_folder: Path,
        *,
        id_labels: Mapping[str, str] = _NO_LABELS,
        config: Path | None = None,
        override_config: Path | None = None,
        secrets_file: Path | None = None,
        remote_env: Mapping[str, str] = _NO_ENV,
        additional_features: str = "",
        skip_post_attach: bool = True,
        on_progress: ProgressCallback | None = None,
    ) -> UpResult:
        """Create/start the container and return its identity.

        ``postAttachCommand`` is skipped by default: Grove is not an editor
        attaching a UI, and that hook runs on every attach — the agent's launch
        is a separate ``exec`` into an already-started container.

        Raises :class:`DevcontainerError` on any failure, carrying the
        ``containerId`` the CLI reported when it had one. That id is the whole
        point: an ``outcome:"error"`` container still exists on the host, and
        losing it here leaks a container nothing can later find.

        There is deliberately no ``mounts`` parameter. Extra mounts belong in
        the generated override config's own ``mounts`` array (see
        :attr:`GroveOverlay.mounts`), because ``--mount`` accepts strictly
        ``type=<bind|volume>,source=,target=[,external=<true|false>]`` and
        rejects the ENTIRE invocation on anything else — including
        ``readonly``, which every agent-config share mount carries. Routing
        them through the config is what makes a read-only bind expressible at
        all; adding the flag back would silently re-break every containerized
        create the moment a caller passes a mount with an option.
        """
        argv = ["up", "--workspace-folder", str(workspace_folder)]
        argv += self._label_flags(id_labels)
        if config is not None:
            argv += ["--config", str(config)]
        if override_config is not None:
            argv += ["--override-config", str(override_config)]
        if secrets_file is not None:
            argv += ["--secrets-file", str(secrets_file)]
        for key, value in remote_env.items():
            argv += ["--remote-env", f"{key}={value}"]
        if additional_features:
            argv += ["--additional-features", additional_features]
        if skip_post_attach:
            argv.append("--skip-post-attach")
        return self.invoke_up(argv, on_progress=on_progress, cwd=workspace_folder)

    def invoke_up(
        self,
        argv: Sequence[str],
        *,
        action: str = "up",
        on_progress: ProgressCallback | None = None,
        cwd: Path | None = None,
    ) -> UpResult:
        """Run an already-assembled ``up`` argv and parse its result.

        Split out of :meth:`up` so the lifecycle layer — which assembles its own
        ``up`` argv from the persisted container identity, the one place the
        teardown/identity invariants are enforced — reuses this process handling
        and result parsing verbatim instead of growing a second copy of it.
        """
        stdout, stderr, code = self._stream(argv, on_progress=on_progress, action=action, cwd=cwd)
        try:
            result = self._parse(stdout, UpResult, action=action)
        except DevcontainerError as exc:
            # No parseable final object at all — the process died before it
            # could report one, so stderr is the only diagnosis available.
            raise DevcontainerError(f"devcontainer {action} failed: {self._tail(stderr)}") from exc
        if code != 0 or result.outcome != "success":
            logger.warning(
                "devcontainer up failed (exit {}, container {}): {}",
                code,
                result.container_id or "-",
                result.failure_detail,
            )
            raise DevcontainerError(
                f"devcontainer {action} failed: {result.failure_detail}",
                container_id=result.container_id,
            )
        logger.info(
            "devcontainer up: container {} ({})",
            result.container_id or "-",
            result.remote_workspace_folder or "-",
        )
        return result

    def exec(
        self,
        workspace_folder: Path,
        argv: Sequence[str],
        *,
        id_labels: Mapping[str, str] = _NO_LABELS,
        config: Path | None = None,
        override_config: Path | None = None,
        remote_env: Mapping[str, str] = _NO_ENV,
    ) -> tuple[int, str]:
        """Run *argv* inside the workspace's container; return ``(code, stdout)``.

        The inner command's non-zero exit is *returned*, not raised — only a
        failure to invoke the CLI itself is an error, the same split
        ``DockerCli.run``/``read`` draws on the lifecycle side.

        Output is CAPTURED rather than inherited, which is not a detail: the
        callers are a probe that needs the answer and a detached agent start
        that needs to report what went wrong, and an inherited stdio would
        additionally spray the CLI's own chatter into whatever terminal or
        journal the daemon happens to own. The pair is returned because "did it
        run" and "what did it say" are both real answers here, and a caller that
        had to infer one from the other would be guessing.

        ``remote_env`` forwards to :meth:`exec_argv` unchanged — an exec has no
        ``--secrets-file`` (that flag reaches lifecycle hooks only), so this is
        the ONLY way env crosses into a command run this way.
        """
        full = [
            *self.exec_argv(
                workspace_folder,
                id_labels=id_labels,
                config=config,
                override_config=override_config,
                remote_env=remote_env,
            ),
            *argv,
        ]
        logger.debug("devcontainer exec: {}", " ".join(full))
        try:
            result = subprocess.run(
                full,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=self._timeout,
                cwd=workspace_folder,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise DevcontainerError(
                f"devcontainer exec in {workspace_folder} failed: {exc}"
            ) from exc
        return result.returncode, result.stdout

    def exec_argv(
        self,
        workspace_folder: Path,
        *,
        id_labels: Mapping[str, str] = _NO_LABELS,
        config: Path | None = None,
        override_config: Path | None = None,
        remote_env: Mapping[str, str] = _NO_ENV,
    ) -> list[str]:
        """The ``devcontainer exec … --`` PREFIX, for a caller that runs it itself.

        Argv rather than a spawned subprocess because a launch backend hosts
        the agent in a tmux pane and has to compose the command itself. Ends
        with ``--``, so the caller appends its own command tokens and nothing it
        appends can be read as a flag.

        The CLI has no ``--workdir``: an exec lands in the configuration's
        ``workspaceFolder``. A caller that needs a nested cwd changes directory
        in the command it appends (see ``DevcontainerLaunchBackend``).
        """
        argv = [self._binary, "exec", "--workspace-folder", str(workspace_folder)]
        argv += self._label_flags(id_labels)
        if config is not None:
            argv += ["--config", str(config)]
        if override_config is not None:
            argv += ["--override-config", str(override_config)]
        for key, value in remote_env.items():
            argv += ["--remote-env", f"{key}={value}"]
        return [*argv, "--"]

    # ─── internal ──────────────────────────────────────────────────────────

    @staticmethod
    def _label_flags(id_labels: Mapping[str, str]) -> list[str]:
        """``--id-label k=v`` per label — the identity ``up``/``exec`` agree on."""
        flags: list[str] = []
        for key, value in id_labels.items():
            flags += ["--id-label", f"{key}={value}"]
        return flags

    def _run(
        self, args: Sequence[str], *, action: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        """One buffered CLI invocation. Non-zero exit is the caller's to judge.

        *cwd* pins the child's working directory; see :meth:`_stream` for why
        inheriting the caller's is a real failure mode rather than untidiness.
        """
        argv = [self._binary, *args, "--log-format", "json"]
        logger.debug("devcontainer: {}", " ".join(argv))
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=self._timeout,
                cwd=cwd,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise DevcontainerError(f"failed to {action}: {exc}") from exc

    def _stream(
        self,
        args: Sequence[str],
        *,
        on_progress: ProgressCallback | None,
        action: str,
        cwd: Path | None = None,
    ) -> tuple[str, str, int]:
        """Run the CLI, feeding each stderr line to *on_progress* as it arrives.

        stderr is drained line-by-line (that is where the CLI's line-delimited
        JSON progress goes) and stdout is collected at the end — safe because
        stdout is ONE final JSON object, kilobytes at most, so it cannot fill
        its pipe while stderr is being read.

        **Pin *cwd*, because the CLI re-spawns its own children against
        whatever directory it inherited, and it inherits ours.** The CLI's
        exec helper resolves each `docker` invocation's directory as
        ``opts.cwd || <its own cwd>``, and **Node reports a spawn whose `cwd`
        does not exist as ``spawn <command> ENOENT``** — naming the command,
        not the directory (verified: `spawn('docker', …, {cwd: '/gone'})`
        yields exactly that). So a `grove create` started from a directory that
        is removed while the build runs — a sibling workspace being torn down,
        say — dies six minutes in with a message that reads as "docker is not
        installed", pointing at PATH, which is fine. The CLI never needs our
        directory: every verb names its target with ``--workspace-folder``.
        """
        argv = [self._binary, *args, "--log-format", "json"]
        logger.debug("devcontainer: {}", " ".join(argv))
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                cwd=cwd,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise DevcontainerError(f"failed to {action}: {exc}") from exc
        captured: list[str] = []
        if proc.stderr is not None:
            for line in proc.stderr:
                if not line.strip():
                    continue
                captured.append(line.rstrip("\n"))
                if on_progress is not None:
                    on_progress(ProgressEvent.parse(line))
        try:
            stdout, stderr = proc.communicate(timeout=self._timeout)
        except subprocess.TimeoutExpired as exc:
            # The bound has to be enforced on the STREAMING verbs too — a cold
            # build is exactly the invocation that can hang forever, and the
            # caller's own deadline (an HTTP client's) firing first is the worst
            # shape here: an orphaned container with no record.
            proc.kill()
            proc.communicate()
            raise DevcontainerError(
                f"devcontainer {action} timed out after {self._timeout}s; "
                f"{self._tail('\n'.join(captured))}"
            ) from exc
        stderr = "\n".join([*captured, stderr.strip()]).strip()
        return stdout, stderr, proc.returncode

    @staticmethod
    def _parse[ModelT: BaseModel](stdout: str, model: type[ModelT], *, action: str) -> ModelT:
        """Parse the LAST JSON object on stdout into *model*.

        Last-wins because the CLI may precede its final result with other
        output; scanning backwards finds the result object without the caller
        ever holding a raw dict.
        """
        for line in reversed([ln for ln in stdout.splitlines() if ln.strip()]):
            try:
                return model.model_validate_json(line)
            except ValueError:
                continue
        raise DevcontainerError(f"{action}: no JSON result on stdout")

    @staticmethod
    def _tail(stderr: str, *, lines: int = 10) -> str:
        """The last few stderr lines — enough to diagnose, short enough to log."""
        return "\n".join(stderr.strip().splitlines()[-lines:]) or "no stderr output"
