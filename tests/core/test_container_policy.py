"""Container autonomy policy: share plans, egress, resource limits.

Pure by construction — no Docker, no network, no real `$HOME`. `AgentSharePlan`
takes an injected `exists` predicate, so the mount tables are asserted against a
fabricated host layout rather than whatever the developer happens to have
installed.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import httpx
import pytest

from grove.core import container_policy, paths
from grove.core.channel import channel_settings_path
from grove.core.config import (
    ContainerConfig,
    EgressConfig,
    GroveConfig,
    RangeSource,
    ResourcesConfig,
)
from grove.core.container_policy import (
    CONTAINER_CONFIG_ROOT,
    CONTAINER_CONTROL_ROOT,
    AgentSharePlan,
    CuratedGitConfig,
    EgressPolicy,
    ResourceLimits,
    TrustStamp,
)
from grove.core.permission import permission_mcp_config_path

# Everything the planner could possibly mount, so a dropped entry is a real
# decision by the planner and never an accident of the fake layout.
_CLAUDE_LAYOUT = (
    "settings.json",
    "skills",
    "commands",
    "plugins",
    "teams",
    "CLAUDE.md",
    ".credentials.json",
    "projects",
    "sessions",
    "history.jsonl",
)
_CODEX_LAYOUT = (
    "config.toml",
    "skills",
    "plugins",
    "rules",
    "memories",
    "auth.json",
    "sessions",
    "state_abc.sqlite",
    "state_abc.sqlite-wal",
    "logs_abc.sqlite",
    "cache",
)


def _plan(
    kind: str, share: str, tmp_path: Path, *, layout: tuple[str, ...] | None = None
) -> AgentSharePlan:
    home = tmp_path / "home"
    dirname = ".claude" if kind == "claude_code" else ".codex"
    names = layout or (_CLAUDE_LAYOUT if kind == "claude_code" else _CODEX_LAYOUT)
    present = {home / dirname / name for name in names}
    return AgentSharePlan.plan(
        kind=kind,  # type: ignore[arg-type]
        share=share,  # type: ignore[arg-type]
        home=home,
        workspace_config_dir=tmp_path / "state" / "agent-config" / "ws1",
        exists=lambda path: path in present,
    )


def _targets(plan: AgentSharePlan) -> dict[str, bool]:
    """SHARED mount basename → readonly: the share payload and nothing else.

    Two kinds of mount are excluded because neither is part of what a *share
    level* exposes, and folding them in would make every level's assertion a
    restatement of the whole table:

    * the config root, always bound (it is the host side of the agent's config
      dir, not a shared host path), pinned by its own tests below;
    * Grove's control files and the hook spool, always bound
      regardless of level — they are Grove's control plane over the agent (and
      its return path), not the user's configuration, and they get their own
      tests below too.
    """
    return {
        mount.target.name: mount.readonly
        for mount in plan.mounts
        if mount.target != plan.container_config_dir
        and mount.target.parent != CONTAINER_CONTROL_ROOT
        and mount.source != plan.hook_spool_dir
    }


# ─── share levels per agent kind ─────────────────────────────────────────────


def test_full_share_mounts_the_measured_claude_payload(tmp_path: Path) -> None:
    """`full` = config read-only, the credential FILE writable, state left out."""
    plan = _plan("claude_code", "full", tmp_path)

    assert _targets(plan) == {
        "settings.json": True,
        "skills": True,
        "commands": True,
        # The host plugin root, beside the config dir and never inside it.
        "plugins-seed": True,
        "teams": True,
        "CLAUDE.md": True,
        # OAuth refresh rewrites it — a :ro mount breaks at the first refresh.
        ".credentials.json": False,
    }
    # State is not config: N workspaces writing one host file is the bug this
    # avoids, and `~/.claude.json` is seeded as a copy instead of mounted.
    assert "projects" not in _targets(plan)
    assert "history.jsonl" not in _targets(plan)
    assert not any(mount.target.name == ".claude.json" for mount in plan.mounts)


def test_full_share_mounts_codex_config_and_auth(tmp_path: Path) -> None:
    plan = _plan("codex", "full", tmp_path)

    assert _targets(plan) == {
        "config.toml": True,
        "skills": True,
        "plugins": True,
        "rules": True,
        "memories": True,
        "auth.json": False,
    }


def test_codex_sqlite_and_sessions_are_never_mounted_at_any_share_level(tmp_path: Path) -> None:
    """Correctness, not privacy: concurrent SQLite over a bind mount corrupts it."""
    for share in ("full", "projects", "isolated"):
        mounted = {mount.source.name for mount in _plan("codex", share, tmp_path).mounts}
        assert not [name for name in mounted if name.startswith(("state_", "logs_"))]
        assert "sessions" not in mounted
        assert "cache" not in mounted


def test_projects_share_is_transcripts_only(tmp_path: Path) -> None:
    """`projects` shares the transcript tree and nothing else — no credentials."""
    plan = _plan("claude_code", "projects", tmp_path)

    assert _targets(plan) == {"projects": False}
    # Codex has no transcript location separable from its never-mounted state dir.
    assert _targets(_plan("codex", "projects", tmp_path)) == {}


def test_isolated_share_mounts_nothing_but_still_points_the_agent_somewhere(
    tmp_path: Path,
) -> None:
    """The credential boundary restored: a clean per-workspace config dir."""
    plan = _plan("claude_code", "isolated", tmp_path)

    assert _targets(plan) == {}
    assert plan.seed_source is None
    assert plan.env == {"CLAUDE_CONFIG_DIR": str(CONTAINER_CONFIG_ROOT / "claude_code")}


def test_absent_host_paths_are_dropped_not_mounted(tmp_path: Path) -> None:
    """Docker materializes a missing bind source as a root-owned host directory."""
    plan = _plan("claude_code", "full", tmp_path, layout=("settings.json", ".credentials.json"))

    assert set(_targets(plan)) == {"settings.json", ".credentials.json"}


def test_kinds_without_a_config_dir_get_an_empty_plan(tmp_path: Path) -> None:
    for kind in ("generic", "mewbo"):
        plan = _plan(kind, "full", tmp_path, layout=())
        assert plan.mounts == ()
        assert plan.env == {}
        assert plan.env_unset == ()


def test_config_dir_env_is_set_and_unset(tmp_path: Path) -> None:
    """Export explicitly AND clear whatever leaked in from the daemon."""
    claude = _plan("claude_code", "full", tmp_path)
    codex = _plan("codex", "full", tmp_path)

    assert claude.env["CLAUDE_CONFIG_DIR"] == str(CONTAINER_CONFIG_ROOT / "claude_code")
    assert "CLAUDE_CONFIG_DIR" in claude.env_unset
    assert codex.env == {"CODEX_HOME": str(CONTAINER_CONFIG_ROOT / "codex")}
    assert codex.env_unset == ("CODEX_HOME",)


def test_mount_flag_shape_carries_readonly(tmp_path: Path) -> None:
    ro = _plan("claude_code", "full", tmp_path, layout=("settings.json",)).mounts[1]
    rw = _plan("claude_code", "full", tmp_path, layout=(".credentials.json",)).mounts[1]

    assert ro.to_flag().endswith(",readonly")
    assert ro.to_flag().startswith("type=bind,source=")
    assert not rw.to_flag().endswith(",readonly")


def test_share_reads_off_the_cascade(tmp_path: Path) -> None:
    cfg = ContainerConfig.model_validate({"agent_config": {"share": "isolated"}})
    plan = AgentSharePlan.from_config(
        cfg,
        kind="claude_code",
        home=tmp_path / "home",
        workspace_config_dir=tmp_path / "ws",
        exists=lambda _path: True,
    )
    assert plan.share == "isolated"
    assert _targets(plan) == {}


# ─── the plugin seed root ────────────────────────────────────────────────────


def _seed_mount(plan: AgentSharePlan) -> container_policy.MountPlan | None:
    return next(
        (mount for mount in plan.mounts if mount.source.name == AgentSharePlan.PLUGIN_SEED_NAME),
        None,
    )


def test_the_host_plugin_root_is_shared_beside_the_writable_one_not_over_it(
    tmp_path: Path,
) -> None:
    """`:ro` over the tool's own plugin root is EROFS on every refresh.

    Claude Code writes marketplaces into `<config dir>/plugins` by `rename()`,
    so the share has to land somewhere else and be NAMED as a seed. The default
    writable root then still resolves inside the `:rw` config-root bind, which
    is why nothing may be mounted at it.
    """
    plan = _plan("claude_code", "full", tmp_path)
    mount = _seed_mount(plan)

    assert mount is not None
    assert mount.readonly
    assert mount.target != plan.container_config_dir / AgentSharePlan.PLUGIN_SEED_NAME
    assert mount.target.parent == plan.container_config_dir
    assert not [m for m in plan.mounts if m.target.name == AgentSharePlan.PLUGIN_SEED_NAME]


def test_the_seed_var_names_the_path_that_was_actually_mounted(tmp_path: Path) -> None:
    """And it is cleared as well as set: a pane inherits the daemon's env."""
    plan = _plan("claude_code", "full", tmp_path)
    mount = _seed_mount(plan)
    var = AgentSharePlan.PLUGIN_SEED_ENV["claude_code"]

    assert mount is not None
    assert plan.env[var] == str(mount.target)
    assert var in plan.env_unset


@pytest.mark.parametrize("share", ["projects", "isolated"])
def test_a_level_that_shares_no_config_seeds_no_plugins(share: str, tmp_path: Path) -> None:
    plan = _plan("claude_code", share, tmp_path)

    assert _seed_mount(plan) is None
    assert AgentSharePlan.PLUGIN_SEED_ENV["claude_code"] not in plan.env


def test_an_absent_host_plugin_root_exports_no_var(tmp_path: Path) -> None:
    """Never name a path nothing mounted — the same shape as an unreachable
    control file, one directory over."""
    plan = _plan("claude_code", "full", tmp_path, layout=("settings.json",))

    assert _seed_mount(plan) is None
    assert AgentSharePlan.PLUGIN_SEED_ENV["claude_code"] not in plan.env


def test_a_kind_with_no_seed_mechanism_shares_its_plugins_unchanged(tmp_path: Path) -> None:
    """Codex has no such env var, so its plugins stay a plain `:ro` config mount."""
    plan = _plan("codex", "full", tmp_path)

    assert _targets(plan)["plugins"] is True
    assert plan.env_unset == ("CODEX_HOME",)
    assert list(plan.env) == ["CODEX_HOME"]


# ─── Grove's own control files ───────────────────────────────────────────────


def _control_plan(tmp_path: Path, *, present: tuple[str, ...] | None = None) -> AgentSharePlan:
    """A plan over three fabricated control files, `present` of which exist."""
    control_dir = tmp_path / "config" / "grove"
    files = tuple(
        control_dir / name
        for name in ("claude-hooks-settings.json", "channel.json", "permission.json")
    )
    on_disk = {control_dir / name for name in (present if present is not None else ())}
    return AgentSharePlan.plan(
        kind="claude_code",
        share="full",
        home=tmp_path / "home",
        workspace_config_dir=tmp_path / "state" / "ws1",
        control_files=files,
        exists=lambda path: path in on_disk,
    )


def test_control_files_are_mounted_readonly_so_the_agent_can_open_the_flags(
    tmp_path: Path,
) -> None:
    """A `--settings <host path>` flag naming a path absent in the container is
    fatal: Claude Code treats a missing settings file as FATAL, so the agent
    exits before printing anything while `grove create` has already exited 0.

    `:ro` because the agent only reads these — a writable mount would let a
    prompt-injected agent rewrite the hook set that then runs in every future
    HOST session, the same reasoning that keeps settings/skills read-only."""
    plan = _control_plan(tmp_path, present=("claude-hooks-settings.json", "channel.json"))

    control = {m.target: m for m in plan.mounts if m.target.parent == CONTAINER_CONTROL_ROOT}
    assert set(control) == {
        CONTAINER_CONTROL_ROOT / "claude-hooks-settings.json",
        CONTAINER_CONTROL_ROOT / "channel.json",
    }
    assert all(mount.readonly for mount in control.values())


def test_control_files_are_mounted_at_every_share_level(tmp_path: Path) -> None:
    """`isolated` isolates the agent from the USER's config; Grove's control
    plane is not that, and an isolated workspace still needs its status hooks."""
    for share in ("full", "projects", "isolated"):
        plan = AgentSharePlan.plan(
            kind="claude_code",
            share=share,  # type: ignore[arg-type]
            home=tmp_path / "home",
            workspace_config_dir=tmp_path / "state" / "ws1",
            control_files=(tmp_path / "cfg" / "hooks.json",),
            exists=lambda _path: True,
        )
        assert any(m.target == CONTAINER_CONTROL_ROOT / "hooks.json" for m in plan.mounts)


def test_the_enclosing_control_directory_is_never_mounted(tmp_path: Path) -> None:
    """The security property, and the reason this is per-FILE (`SHARED_RW`'s rule).

    Grove's three control files sit in its user config dir alongside
    `webapp-sessions.json`, which stores live daemon bearer tokens in plaintext.
    Binding the directory — even `:ro` — would hand a relaxed-permissions agent
    control over every workspace in every repo on the machine, which is an
    escalation clean out of the blast radius this module draws, and one no
    amount of read-only helps with.
    """
    control_dir = tmp_path / "config" / "grove"
    plan = _control_plan(tmp_path, present=("claude-hooks-settings.json",))

    assert all(mount.source != control_dir for mount in plan.mounts)
    assert all(mount.target != CONTAINER_CONTROL_ROOT for mount in plan.mounts)
    assert all(mount.source.is_file() or mount.source.name for mount in plan.mounts)


def test_container_control_path_translates_only_what_is_actually_mounted(
    tmp_path: Path,
) -> None:
    """The answer is read off the mount table, not recomputed from the same rule.

    An absent control file is dropped (Docker would materialize the missing bind
    source as a root-owned DIRECTORY at the exact path Grove's own writer later
    needs), and the translation must then say so — a flag emitted for it names a
    file the agent cannot open, which fatally exits the agent at start.
    """
    control_dir = tmp_path / "config" / "grove"
    plan = _control_plan(tmp_path, present=("claude-hooks-settings.json",))

    assert plan.container_control_path(control_dir / "claude-hooks-settings.json") == (
        CONTAINER_CONTROL_ROOT / "claude-hooks-settings.json"
    )
    # Dropped at plan time → unreachable, and honestly reported as such.
    assert plan.container_control_path(control_dir / "channel.json") is None
    # Never planned at all: a same-named file from somewhere else must not
    # borrow another file's mount just by sharing a basename.
    assert plan.container_control_path(tmp_path / "elsewhere" / "channel.json") is None


def test_default_control_files_are_read_off_their_writers() -> None:
    """DRY seam: the table names no filenames, so a file that moves moves once."""
    assert AgentSharePlan.default_control_files() == (
        paths.agent_hooks_settings_path(),
        paths.agent_container_settings_path(),
    )
    assert AgentSharePlan.host_process_control_files() == (
        channel_settings_path(),
        permission_mcp_config_path(),
    )


def test_a_control_file_that_spawns_a_host_process_is_never_mounted(tmp_path: Path) -> None:
    """Reachability is TWO questions — can the agent open the file, and can
    it run what the file names.

    `--channels` and `--mcp-config` both register a stdio server whose command is
    `sys.executable`: the host interpreter of whatever rendered them. The FILE is
    perfectly mountable, so a naive translation would hand back a valid container
    path — and the agent would register a command no container has. Withholding
    the mount is what makes `container_control_path` say `None`, which the launch
    already reads as *omit the flag*.

    `exists` says yes to everything, so anything absent here was dropped by the
    table and not by the fake layout.
    """
    plan = AgentSharePlan.plan(
        kind="claude_code",
        share="full",
        home=tmp_path / "home",
        workspace_config_dir=tmp_path / "state" / "ws1",
        exists=lambda _path: True,
    )

    for host_only in AgentSharePlan.host_process_control_files():
        assert plan.container_control_path(host_only) is None
        assert all(mount.source != host_only for mount in plan.mounts)
    # ...and the namespace-agnostic one still crosses, or this passes by
    # refusing every control file rather than by drawing the distinction.
    hooks = paths.agent_hooks_settings_path()
    assert plan.container_control_path(hooks) == CONTAINER_CONTROL_ROOT / hooks.name


def test_the_seed_carries_no_mcp_server_the_container_cannot_start() -> None:
    """The host's `mcpServers` is a HOST registry, and a naive copy rides it verbatim.

    Grove's own `grove-mcp` entry, for example, is a console script of the
    host's `uv tool` venv that nothing mounts or installs in-container. A
    container agent seeded with it would come up with a server registered
    that can never start — either a silent capability gap or a startup
    error, and live-looking config either way. Sign-in still carries, which
    is the only thing the seed is for.
    """
    seeded = AgentSharePlan.curate(
        {
            "oauthAccount": {"emailAddress": "dev@example.test"},
            "projects": {"/a": {}},
            "mcpServers": {
                "grove": {"type": "stdio", "command": "~/.local/bin/grove-mcp", "args": []},
                "remote": {"type": "http", "url": "https://mcp.example.test"},
            },
        }
    )

    assert seeded == {"oauthAccount": {"emailAddress": "dev@example.test"}}


def test_kinds_without_a_config_dir_get_no_control_mounts(tmp_path: Path) -> None:
    """Every control flag is claude_code's, so mounting for mewbo/generic would
    be machinery for a path that cannot be taken."""
    for kind in ("generic", "mewbo"):
        plan = AgentSharePlan.plan(
            kind=kind,  # type: ignore[arg-type]
            share="full",
            home=tmp_path / "home",
            workspace_config_dir=tmp_path / "ws",
            control_files=(tmp_path / "cfg" / "hooks.json",),
            exists=lambda _path: True,
        )
        assert plan.mounts == ()
        assert plan.container_control_path(tmp_path / "cfg" / "hooks.json") is None


# ─── the config root itself: the host side of the bridge ────────────────────


def test_the_config_root_is_bound_writable_and_first(tmp_path: Path) -> None:
    """Without this mount the agent's own writes — its TRANSCRIPT above all, and
    the seeded `.claude.json` — live and die inside the container, which is what
    left a containerized workspace with no agent axis at all. It comes first so
    the shared host paths layer on top of it, and it is `:rw` because it is
    Grove's own per-workspace directory, not the user's config."""
    for share in ("full", "projects", "isolated"):
        plan = _plan("claude_code", share, tmp_path)
        root = plan.mounts[0]

        assert root.target == plan.container_config_dir
        assert root.source == plan.host_config_dir
        assert root.readonly is False


def test_the_transcript_reads_back_from_the_workspace_dir(tmp_path: Path) -> None:
    """The host half of the namespace bridge: the agent writes its
    transcript under the container config root, which IS this host directory."""
    for kind, share in (("claude_code", "full"), ("claude_code", "isolated"), ("codex", "full")):
        plan = _plan(kind, share, tmp_path)
        assert plan.transcript_host_dir == plan.host_config_dir


def test_the_projects_share_shadows_the_workspace_dir_for_transcripts(tmp_path: Path) -> None:
    """`share: projects` binds the REAL host transcript tree over the config
    root's own, so the agent's transcripts land in `~/.claude`, not here — read
    off the mount table rather than assumed, because getting it from the share
    level would silently break the moment the table changes."""
    plan = _plan("claude_code", "projects", tmp_path)

    assert plan.transcript_host_dir == tmp_path / "home" / ".claude"


def test_a_kind_with_no_config_dir_has_no_transcript_bridge(tmp_path: Path) -> None:
    """`None` is the honest answer, and the read side needs it: it acts on what
    is stored, so a wrong directory is worse than none."""
    for kind in ("generic", "mewbo"):
        assert _plan(kind, "full", tmp_path, layout=()).transcript_host_dir is None


def test_the_config_dir_is_created_even_when_nothing_is_seeded(tmp_path: Path) -> None:
    """It is a bind SOURCE: Docker materializes a missing one as a root-owned
    host directory the in-container agent then cannot write into."""
    plan = AgentSharePlan.plan(
        kind="claude_code",
        share="isolated",  # seeds nothing
        home=tmp_path / "home",
        workspace_config_dir=tmp_path / "state" / "ws1",
        exists=lambda _path: False,
    )

    assert plan.seed() is None
    assert plan.host_config_dir.is_dir()


# ─── the hook spool: the control plane's return path ────────────────────────


def _spool_plan(tmp_path: Path, *, share: str = "full") -> AgentSharePlan:
    return AgentSharePlan.plan(
        kind="claude_code",
        share=share,  # type: ignore[arg-type]
        home=tmp_path / "home",
        workspace_config_dir=tmp_path / "state" / "ws1",
        control_files=(),
        hook_spool_dir=tmp_path / "state" / "agent-sidecars" / "spool",
        exists=lambda _path: True,
    )


def test_the_hook_spool_is_bound_writable_at_its_own_host_path(tmp_path: Path) -> None:
    """The one mount bound at its HOST path rather than under `/grove`.

    That is what lets a single rendered hook command work in both namespaces: a
    containerized agent has no `grove-agent-hook`, so it falls back to spooling
    the raw payload, and the path named in the settings file — written once, on
    the host — has to mean the same thing on both sides of the boundary. A value
    that must resolve identically in two namespaces can only be preserved, never
    translated (the git-common-dir idiom, same reason).
    """
    spool = tmp_path / "state" / "agent-sidecars" / "spool"
    mount = next(m for m in _spool_plan(tmp_path).mounts if m.source == spool)

    assert mount.target == PurePosixPath(spool.as_posix())
    assert mount.readonly is False


def test_the_hook_spool_is_bound_at_every_share_level(tmp_path: Path) -> None:
    """`isolated` isolates the agent from the USER's config; the status hooks it
    still installs need somewhere to land."""
    for share in ("full", "projects", "isolated"):
        plan = _spool_plan(tmp_path, share=share)
        assert any(m.source == plan.hook_spool_dir for m in plan.mounts)


def test_the_hook_spool_is_created_by_seed(tmp_path: Path) -> None:
    """Same hazard as the config root, one directory over: an absent bind source
    is materialized root-owned, and the writer here is a shell redirect running
    as the container's user — it would fail on every single hook event."""
    plan = _spool_plan(tmp_path)

    plan.seed()

    assert plan.hook_spool_dir is not None
    assert plan.hook_spool_dir.is_dir()


def test_kinds_without_a_config_dir_get_no_hook_spool(tmp_path: Path) -> None:
    """The hooks are claude_code's; mewbo/generic get no mounts at all."""
    for kind in ("generic", "mewbo"):
        plan = AgentSharePlan.plan(
            kind=kind,  # type: ignore[arg-type]
            share="full",
            home=tmp_path / "home",
            workspace_config_dir=tmp_path / "ws",
            hook_spool_dir=tmp_path / "spool",
            exists=lambda _path: True,
        )
        assert plan.mounts == ()


# ─── the seeded ~/.claude.json copy ─────────────────────────────────────────


def test_seed_copies_signin_and_drops_other_workspaces_history(tmp_path: Path) -> None:
    """Sign-in carries; an in-container rewrite dies with the workspace."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {"accountUuid": "u-1"},
                "tipsHistory": {"new-user-warmup": 1},
                "projects": {"/some/other/repo": {"history": ["secret"]}},
            }
        ),
        encoding="utf-8",
    )
    plan = AgentSharePlan.plan(
        kind="claude_code",
        share="full",
        home=home,
        workspace_config_dir=tmp_path / "ws",
        exists=lambda _path: False,
    )

    written = plan.seed()

    assert written == tmp_path / "ws" / ".claude.json"
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["oauthAccount"] == {"accountUuid": "u-1"}
    # Anything that is neither history nor a host-namespace registration rides
    # along untouched — the drop list is a table, not a filter on the copy.
    assert payload["tipsHistory"] == {"new-user-warmup": 1}
    assert "projects" not in payload


def test_seed_is_idempotent_and_never_reverts_an_in_container_change(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"a": 1}}), encoding="utf-8")
    plan = AgentSharePlan.plan(
        kind="claude_code",
        share="full",
        home=home,
        workspace_config_dir=tmp_path / "ws",
        exists=lambda _path: False,
    )
    first = plan.seed()
    assert first is not None
    first.write_text(json.dumps({"mine": True}), encoding="utf-8")

    plan.seed()

    assert json.loads(first.read_text(encoding="utf-8")) == {"mine": True}


def test_seed_degrades_to_empty_when_the_host_file_is_unreadable(tmp_path: Path) -> None:
    """A workspace with no sign-in carried is degraded, not a failed create."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text("{not json", encoding="utf-8")
    plan = AgentSharePlan.plan(
        kind="claude_code",
        share="full",
        home=home,
        workspace_config_dir=tmp_path / "ws",
        exists=lambda _path: False,
    )

    written = plan.seed()

    assert written is not None
    assert json.loads(written.read_text(encoding="utf-8")) == {}


def test_isolated_and_codex_seed_nothing(tmp_path: Path) -> None:
    assert _plan("claude_code", "isolated", tmp_path).seed() is None
    assert _plan("codex", "full", tmp_path).seed() is None


# ─── the trust stamp ─────────────────────────────────────────────────────────


def _seed_plan(tmp_path: Path, *, share: str = "full", kind: str = "claude_code") -> AgentSharePlan:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "oauthAccount": {"accountUuid": "u-1"},
                "projects": {"/home/dev/some-other-repo": {"hasTrustDialogAccepted": True}},
            }
        ),
        encoding="utf-8",
    )
    return AgentSharePlan.plan(
        kind=kind,  # type: ignore[arg-type]
        share=share,  # type: ignore[arg-type]
        home=home,
        workspace_config_dir=tmp_path / "ws",
        exists=lambda _path: False,
    )


def _seeded(plan: AgentSharePlan, trust: TrustStamp | None) -> dict[str, object]:
    written = plan.seed(trust=trust)
    assert written is not None
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_the_seed_stamps_the_folder_the_container_actually_reports(tmp_path: Path) -> None:
    """The dialog is keyed on the absolute cwd, and only the CLI knows it.

    A container workspace is always a path the tool has never seen, and the
    agent Grove starts there is unattended — so the trust prompt is one nobody
    is present to answer and the workspace never begins working.
    """
    payload = _seeded(_seed_plan(tmp_path), TrustStamp(workspace_folder="/workspaces/repo"))

    assert payload["projects"] == {
        "/workspaces/repo": {
            "hasTrustDialogAccepted": True,
            "hasCompletedProjectOnboarding": True,
            "projectOnboardingSeenCount": 1,
            "hasClaudeMdExternalIncludesApproved": True,
            "enabledMcpjsonServers": [],
        }
    }
    # Sign-in still carries — the stamp rides the same seed, it doesn't replace it.
    assert payload["oauthAccount"] == {"accountUuid": "u-1"}


def test_the_stamp_is_a_fresh_map_and_never_the_hosts_project_list(tmp_path: Path) -> None:
    """`projects` names every repo the user works on, and not one of those paths
    exists in this container — so the drop stays and the stamp is built fresh."""
    payload = _seeded(_seed_plan(tmp_path), TrustStamp(workspace_folder="/workspaces/repo"))

    assert list(payload["projects"]) == ["/workspaces/repo"]  # type: ignore[arg-type]


def test_no_stamp_leaves_the_seed_exactly_as_it_was(tmp_path: Path) -> None:
    """`trust: false` is the operator asking for the prompt back."""
    payload = _seeded(_seed_plan(tmp_path), None)

    assert "projects" not in payload


def test_an_isolated_workspace_is_stamped_even_though_it_seeds_nothing(tmp_path: Path) -> None:
    """`isolated` carries no sign-in — and is therefore the level whose config
    dir is virgin, so it is the one that most certainly meets the dialog."""
    plan = _seed_plan(tmp_path, share="isolated")
    assert plan.seed_source is None

    payload = _seeded(plan, TrustStamp(workspace_folder="/workspaces/repo"))

    assert payload == {
        "projects": {
            "/workspaces/repo": {
                "hasTrustDialogAccepted": True,
                "hasCompletedProjectOnboarding": True,
                "projectOnboardingSeenCount": 1,
                "hasClaudeMdExternalIncludesApproved": True,
                "enabledMcpjsonServers": [],
            }
        }
    }


def test_a_kind_with_no_trust_gate_is_never_stamped(tmp_path: Path) -> None:
    """One row in the table today; a tool with no per-folder gate gets nothing,
    and the seed file it does not have is not invented for it."""
    for kind in ("codex", "generic"):
        plan = _seed_plan(tmp_path, kind=kind)
        assert plan.seed(trust=TrustStamp(workspace_folder="/workspaces/repo")) is None


def test_the_stamp_approves_the_worktrees_own_mcp_servers(tmp_path: Path) -> None:
    """MCP approval lives in the SAME per-folder block, so a trusted folder with
    an unapproved server list just trades one blocking prompt for another. The
    names come from the committed `.mcp.json` the worktree carries — the one
    registry a container can actually reach."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"gitea": {"type": "http"}, "docs": {"type": "stdio"}}}),
        encoding="utf-8",
    )

    stamp = TrustStamp.for_worktree("/workspaces/repo", worktree=worktree)

    assert stamp.mcp_servers == ("gitea", "docs")
    payload = _seeded(_seed_plan(tmp_path), stamp)
    entry = payload["projects"]["/workspaces/repo"]  # type: ignore[index]
    assert entry["enabledMcpjsonServers"] == ["gitea", "docs"]


def test_a_worktree_with_no_mcp_config_approves_nothing(tmp_path: Path) -> None:
    assert TrustStamp.for_worktree("/workspaces/repo", worktree=tmp_path).mcp_servers == ()


def test_the_stamp_refreshes_on_an_existing_seed_without_reverting_it(tmp_path: Path) -> None:
    """The copy is idempotent by existence; the stamp is the exception, and it
    is a merge — a workspace created before the stamp existed, or one whose
    `.mcp.json` gained a server, gets it at its next start, while the tool's own
    per-folder bookkeeping survives."""
    plan = _seed_plan(tmp_path)
    first = plan.seed()
    assert first is not None
    first.write_text(
        json.dumps(
            {
                "mine": True,
                "projects": {"/workspaces/repo": {"lastCost": 4, "hasTrustDialogAccepted": False}},
            }
        ),
        encoding="utf-8",
    )

    plan.seed(trust=TrustStamp(workspace_folder="/workspaces/repo", mcp_servers=("gitea",)))

    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["mine"] is True
    assert payload["projects"]["/workspaces/repo"] == {
        "lastCost": 4,
        "hasTrustDialogAccepted": True,
        "hasCompletedProjectOnboarding": True,
        "projectOnboardingSeenCount": 1,
        "hasClaudeMdExternalIncludesApproved": True,
        "enabledMcpjsonServers": ["gitea"],
    }


def test_an_unreadable_existing_seed_is_never_clobbered_by_the_stamp(tmp_path: Path) -> None:
    """The stamp is worth an interactive prompt, never somebody's config file."""
    plan = _seed_plan(tmp_path)
    target = tmp_path / "ws" / ".claude.json"
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")

    plan.seed(trust=TrustStamp(workspace_folder="/workspaces/repo"))

    assert target.read_text(encoding="utf-8") == "{not json"


# ─── curated gitconfig ──────────────────────────────────────────────────────


def test_curated_gitconfig_keeps_identity_and_drops_credential_helpers() -> None:
    curated = CuratedGitConfig.curate(
        {
            "user.name": "Dev",
            "user.email": "dev@example.test",
            "credential.helper": "store --file /home/dev/.git-credentials",
            "gpg.program": "/usr/local/bin/gpg-wrapper",
            "core.editor": "",
        }
    )

    keys = [key for key, _ in curated.entries]
    assert "credential.helper" not in keys
    assert "gpg.program" not in keys
    # Empty host values are not forwarded as empty settings.
    assert "core.editor" not in keys
    assert ("user.name", "Dev") in curated.entries
    # Without safe.directory every in-container git call fails on ownership.
    assert ("safe.directory", "*") in curated.entries


def test_curated_gitconfig_env_is_a_complete_git_config_count_block() -> None:
    env = CuratedGitConfig.curate({"user.email": "dev@example.test"}).to_env()

    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert env["GIT_CONFIG_VALUE_0"] == "*"
    assert env["GIT_CONFIG_KEY_1"] == "user.email"
    assert env["GIT_CONFIG_VALUE_1"] == "dev@example.test"


# ─── egress ─────────────────────────────────────────────────────────────────


def test_allowlist_is_derived_from_kind_packages_remotes_and_grove() -> None:
    """Normal dev work needs zero config; a LAN forge rides `git remote`."""
    policy = EgressPolicy.derive(
        EgressConfig(allow=("docs.internal.example", "10.0.0.0/8")),
        kind="claude_code",
        remote_urls=("http://git.forge.internal/org/repo.git", "git@git.forge.internal:o/r.git"),
    )

    assert "api.anthropic.com" in policy.hosts
    assert "registry.npmjs.org" in policy.hosts
    assert "host.docker.internal" in policy.hosts
    assert "git.forge.internal" in policy.hosts
    assert "docs.internal.example" in policy.hosts
    assert policy.cidrs == ("10.0.0.0/8",)
    # A different kind gets a different agent plane, not a union of every provider.
    assert "api.openai.com" not in policy.hosts


def test_remote_host_parses_url_and_scp_forms() -> None:
    assert EgressPolicy.remote_host("https://example.test/org/repo.git") == "example.test"
    assert EgressPolicy.remote_host("ssh://git@example.test:2222/o/r.git") == "example.test"
    assert EgressPolicy.remote_host("git@example.test:org/repo.git") == "example.test"
    assert EgressPolicy.remote_host("/srv/local/repo.git") == ""
    assert EgressPolicy.remote_host("") == ""


def test_firewall_script_allows_the_derived_set_and_defaults_to_drop() -> None:
    policy = EgressPolicy.derive(
        EgressConfig(allow=("192.168.5.0/24",)), kind="codex", remote_urls=()
    )
    script = policy.firewall_script()

    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script
    assert "iptables -P OUTPUT DROP" in script
    assert "api.openai.com" in script
    # Destinations go through the one `allow` helper, which emits BOTH chains —
    # the originating one and the nested one — so the two cannot drift.
    assert 'allow "192.168.5.0/24"' in script
    assert 'iptables -A OUTPUT -d "$1" -j ACCEPT' in script
    assert 'iptables -A DOCKER-USER -d "$1" -j RETURN' in script
    # DNS is a documented ceiling, opened deliberately.
    assert "iptables -A OUTPUT -p udp --dport 53 -j ACCEPT" in script
    # ipset is absent on the reference host — plain chains only.
    assert "ipset" not in script
    # Own-subnet reachability comes from the container's interfaces, never a guess.
    assert "ip -o -f inet addr show" in script


def test_deny_mode_allows_only_loopback_and_the_workspace_network() -> None:
    script = EgressPolicy.derive(EgressConfig(mode="deny"), kind="claude_code").firewall_script()

    assert "iptables -P OUTPUT DROP" in script
    assert "--dport 53" not in script
    assert "api.anthropic.com" not in script


def test_open_mode_emits_nothing_and_does_not_fail_closed(tmp_path: Path) -> None:
    """`open` is supported, not nagged: no script, nothing that can fail a start."""
    policy = EgressPolicy.derive(EgressConfig(mode="open"), kind="claude_code")

    assert policy.firewall_script() == ""
    assert policy.post_start_command() == ""
    assert policy.write_script(tmp_path) is None
    assert policy.fail_closed is False


def test_non_open_modes_fail_closed_and_verify_the_ruleset() -> None:
    for mode in ("allowlist", "deny"):
        policy = EgressPolicy.derive(EgressConfig(mode=mode), kind="claude_code")  # type: ignore[arg-type]
        script = policy.firewall_script()

        assert policy.fail_closed is True
        # Two independent proofs, both exiting non-zero: the policy line is real,
        # and a known-blocked destination is actually unreachable.
        assert "exit 1" in script
        assert EgressPolicy.VERIFY_BLOCKED in script
        assert "grove: egress firewall did not apply" in script


def test_write_script_lands_next_to_the_generated_override(tmp_path: Path) -> None:
    policy = EgressPolicy.derive(EgressConfig(), kind="claude_code")

    written = policy.write_script(tmp_path)

    assert written == tmp_path / EgressPolicy.SCRIPT_RELPATH
    assert written.read_text(encoding="utf-8") == policy.firewall_script()
    assert str(EgressPolicy.SCRIPT_RELPATH) in policy.post_start_command()


def test_egress_needs_net_admin() -> None:
    assert EgressPolicy.CAP_ADD == ("NET_ADMIN", "NET_RAW")


@pytest.mark.parametrize("mode", ["allowlist", "deny"])
def test_an_image_without_iptables_is_refused_with_a_diagnosis_not_a_127(
    mode: str, tmp_path: Path
) -> None:
    """RUNS the generated script with an empty PATH — the slim-image case.

    Not an assertion about the script's text: `set -euo pipefail` plus a
    missing `iptables` would otherwise produce a bare `command not found` and
    exit 127, surfacing to the user as `postStartCommand failed with exit code
    127` and no workspace at all. `iptables` is absent from `python:*-slim`,
    `node:*`, alpine, and plain ubuntu — and from the image Grove's own
    packaged default names, which only gets it because the Features chain
    happens to drag it in.

    The refusal is deliberate (`fail_closed` is the contract the user asked
    for); what is asserted here is that it now names the missing binary and
    the way out, and that it happens BEFORE any rule is touched.
    """
    bash = shutil.which("bash")
    assert bash is not None, "bash is required to exercise the generated script"
    policy = EgressPolicy.derive(EgressConfig(mode=mode), kind="claude_code")  # type: ignore[arg-type]
    script = tmp_path / "egress.sh"
    script.write_text(policy.firewall_script(), encoding="utf-8")

    # An empty PATH is the slim image: no iptables, no ip, nothing.
    result = subprocess.run(
        [bash, str(script)],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": str(tmp_path / "empty")},
    )

    assert result.returncode == 1, result.stderr
    assert "command not found" not in result.stderr
    assert "iptables" in result.stderr
    assert f"egress mode '{mode}'" in result.stderr
    # The escape hatch is named, or the user is stuck with a workspace that
    # cannot start and no supported way forward.
    assert "container.egress.mode = 'open'" in result.stderr


def test_the_binary_probe_runs_before_any_rule_is_touched() -> None:
    """Ordering is the property: a probe after the first `iptables -F` is no probe."""
    script = EgressPolicy.derive(EgressConfig(mode="deny"), kind="claude_code").firewall_script()

    assert script.index("command -v") < script.index("iptables -F OUTPUT")


@pytest.mark.parametrize("mode", ["allowlist", "deny"])
def test_ipv6_egress_is_denied_in_every_non_open_mode(mode: str) -> None:
    """An allowlist scoped to IPv4 only lets v6 traffic bypass the boundary entirely.

    Asserted for BOTH non-open modes because the gap is invisible in either —
    the v4 rules stay present and correct, and `ip6tables -P OUTPUT` simply read
    ACCEPT. Only `open` (no script at all) is exempt.
    """
    policy = EgressPolicy.derive(EgressConfig(mode=mode), kind="claude_code")  # type: ignore[arg-type]
    script = policy.firewall_script()

    assert "ip6tables -P OUTPUT DROP" in script
    assert "ip6tables -A OUTPUT -o lo -j ACCEPT" in script
    # NDP survives: link-local never leaves the segment, so dropping it tightens
    # nothing and breaks the local network in ways that read as Grove bugs.
    assert "ip6tables -A OUTPUT -d fe80::/10 -j ACCEPT" in script
    assert "ip6tables -A OUTPUT -d ff02::/16 -j ACCEPT" in script


def test_ipv6_denial_fails_closed_like_the_ipv4_arm() -> None:
    """The v6 policy line is PROVEN, not assumed — same discipline as v4."""
    script = EgressPolicy.derive(EgressConfig(), kind="claude_code").firewall_script()

    assert 'rules="$(ip6tables -S OUTPUT)"' in script
    assert "grove: ipv6 egress firewall did not apply" in script
    # `ip6tables` is probed with the others, before any rule is touched, so a
    # slim image is refused with a diagnosis rather than dying mid-ruleset with
    # the v4 half already applied.
    assert "ip6tables" in EgressPolicy.REQUIRED_BINARIES
    assert script.index("command -v") < script.index("ip6tables -F OUTPUT")


def test_ipv6_is_rejected_not_blackholed() -> None:
    """A DROPped v6 SYN is a multi-second stall, not merely a policy gap.

    A dual-stack client tries v6 first and waits out its full connect timeout
    before falling back (measured: 15s, where forced v4 returned in 60ms). An
    ICMPv6 rejection fails `connect()` immediately. It is a latency fix, not the
    boundary — hence best-effort against a kernel with no REJECT target, which
    must NOT fail a start that is already denied by policy.
    """
    script = EgressPolicy.derive(EgressConfig(), kind="claude_code").firewall_script()

    assert "ip6tables -A OUTPUT -j REJECT --reject-with adm-prohibited" in script
    reject = script.index("-j REJECT")
    assert "2>/dev/null ||" in script[reject : reject + 120]
    # The REJECT rule must precede the policy line it is a courtesy to, or it is
    # appended after a chain that already ends in DROP and never matches.
    assert reject < script.index("ip6tables -P OUTPUT DROP")


def test_a_kernel_without_ipv6_skips_the_arm_rather_than_failing_the_start() -> None:
    """No IPv6 stack is a verified ABSENCE of the thing being filtered.

    `ipv6.disable=1` leaves `ip6tables` installed but unable to initialize its
    table, so an ungated arm would turn `set -e` into a failed container start on
    a machine with no IPv6 to leak through. That is not the fail-open direction:
    a missing binary (an inability to filter) still refuses.
    """
    script = EgressPolicy.derive(EgressConfig(), kind="claude_code").firewall_script()

    assert f"if [ -e {EgressPolicy.IPV6_STACK_PROBE} ]; then" in script
    assert EgressPolicy.IPV6_STACK_PROBE == "/proc/net/if_inet6"
    assert "grove: kernel reports no IPv6 stack; nothing to filter" in script


@pytest.mark.parametrize("mode", ["allowlist", "deny"])
def test_nested_container_egress_is_filtered_in_docker_user(mode: str) -> None:
    """An OUTPUT-only allowlist lets docker-in-docker bypass it entirely.

    A nested daemon's containers have their traffic ROUTED by the workspace
    container, not originated by it, so none of it traverses OUTPUT. Measured
    side by side in one container: workspace `1.1.1.1:443` BLOCKED, nested
    `1.1.1.1:443` OPEN.
    """
    policy = EgressPolicy.derive(EgressConfig(mode=mode), kind="claude_code")  # type: ignore[arg-type]
    script = policy.firewall_script()

    assert "iptables -N DOCKER-USER 2>/dev/null || true" in script
    assert "iptables -F DOCKER-USER" in script
    # The terminal deny is scoped to the external interface, and RETURN (not
    # ACCEPT) so allowed traffic still falls through to docker's own chains.
    assert 'iptables -A DOCKER-USER -o "$ext" -j DROP' in script
    verdicts = [
        line for line in script.splitlines() if "-A DOCKER-USER" in line and "-j ACCEPT" in line
    ]
    assert verdicts == [], verdicts


def test_nested_traffic_that_never_leaves_the_container_is_untouched() -> None:
    """Why this is not simply `-P FORWARD DROP`.

    Nested container-to-container traffic is forwarded too and never leaves the
    workspace container — it is inside the blast radius by construction, not
    egress. A blanket drop would break nested compose stacks and the nested
    daemon's embedded DNS while bounding nothing extra. Verified on a real
    nested daemon: nested→nested and workspace→nested both still connect.
    """
    script = EgressPolicy.derive(EgressConfig(), kind="claude_code").firewall_script()

    assert 'iptables -A DOCKER-USER ! -o "$ext" -j RETURN' in script
    assert "iptables -P FORWARD DROP" not in script
    # The discriminator is the interface carrying the default route, resolved
    # at apply time; not knowing it is an inability to filter, so it refuses.
    assert "ext=$(ip route show default" in script
    assert "grove: cannot determine the external interface" in script


def test_the_nested_policy_lives_where_dockerd_cannot_flush_it() -> None:
    """`DOCKER-USER`, not `FORWARD` — verified on Docker 29.6.1.

    Rules placed in `DOCKER-USER` before `dockerd` first starts survive its
    startup, and `dockerd` adds the `FORWARD -j DOCKER-USER` jump itself. That
    is what makes the policy immune to the ordering war between Grove's
    `postStartCommand` and the nested daemon's start. Owning FORWARD directly
    would lose that race; flushing it afterwards would tear out the nested
    daemon's own networking.
    """
    script = EgressPolicy.derive(EgressConfig(), kind="claude_code").firewall_script()

    assert "iptables -F FORWARD" not in script
    # The jump is added only when absent, so a workspace that never starts a
    # nested daemon still has a policy chain something reaches.
    assert 'if ! iptables -S FORWARD | grep -q -- "-j DOCKER-USER"; then' in script
    assert "iptables -A FORWARD -j DOCKER-USER" in script


def test_the_self_check_cannot_pass_while_the_nested_arm_is_missing() -> None:
    """A canary proves only the namespace it runs in.

    `1.1.1.1:443` is unreachable from the workspace container while reachable
    from a nested one at the same instant, so a probe run from the wrong
    namespace stays green across a live bypass. It cannot be fixed by probing
    harder: a real nested probe needs a nested daemon that may not have
    started and an image pulled through the network just firewalled. So the
    nested arm is asserted STRUCTURALLY, and the probe states what it does
    not cover.
    """
    script = EgressPolicy.derive(EgressConfig(), kind="claude_code").firewall_script()

    assert "grove: nested-container egress policy did not apply" in script
    assert "grove: nested-container egress policy is not reachable from FORWARD" in script
    assert "this proves" in script and "ORIGINATING namespace only" in script


def test_the_fail_closed_check_survives_a_large_ruleset() -> None:
    """A self-check that fails on a CORRECT firewall is as bad as one that passes
    on a broken one — both teach the operator to distrust it.

    `iptables -S OUTPUT | head -1 | grep -q` is unsafe: both `head -1` and
    `grep -q` exit at the first match and close the pipe, so `iptables` dies of
    SIGPIPE (141), which `set -o pipefail` promotes to the pipeline's status —
    misreporting `grove: egress firewall did not apply` on a ruleset with
    `-P OUTPUT DROP` sitting in the very output being tested, for any ruleset
    large enough to fill the pipe buffer (i.e. every real workspace).
    """
    script = EgressPolicy.derive(EgressConfig(), kind="claude_code").firewall_script()

    assert "| head -1 | grep -q" not in script
    # Command substitution reads to EOF; `case` is a builtin, so there is no
    # second process left to kill mid-write.
    assert 'rules="$(iptables -S OUTPUT)"' in script
    assert 'case "$rules" in' in script


def test_open_mode_denies_nothing_on_either_family(tmp_path: Path) -> None:
    """`open` stays the un-nagged opt-out — v6 denial must not sneak into it."""
    assert (
        EgressPolicy.derive(EgressConfig(mode="open"), kind="claude_code").firewall_script() == ""
    )


# ─── resource limits ────────────────────────────────────────────────────────


def test_resource_limits_emit_docker_update_argv() -> None:
    limits = ResourceLimits.from_config(
        ResourcesConfig(memory="8g", cpus="4", pids=2048),
    )

    assert limits.docker_update_argv("grove-ws1") == (
        "docker",
        "update",
        "--memory",
        "8g",
        "--memory-swap",
        "8g",
        "--cpus",
        "4",
        "--pids-limit",
        "2048",
        "grove-ws1",
    )


def test_partial_resource_config_emits_only_what_it_names() -> None:
    limits = ResourceLimits.from_config(ResourcesConfig(memory="512m"))

    assert limits.docker_update_argv("c", docker_bin="/usr/bin/docker") == (
        "/usr/bin/docker",
        "update",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "c",
    )


def test_unconfigured_resources_are_a_no_op() -> None:
    limits = ResourceLimits.from_config(ResourcesConfig())

    assert limits.empty
    assert limits.docker_update_argv("c") == ()


def test_a_memory_cap_always_carries_its_memory_swap_twin() -> None:
    """`--memory` alone is rejected by Docker, so the memory cap never applies unpaired.

    Verified against Docker 29.6.1: `docker update --memory 512m` on a freshly
    created container fails with *"Memory limit should be smaller than already
    set memoryswap limit, update the memoryswap at the same time"*, because a
    container the devcontainer CLI just created has no memory-swap limit set.
    The caller is best-effort by design, so this only logs one line and
    carries on — `cpus` and `pids` need no pairing and work regardless.

    Equal values are docker's spelling of "no swap beyond the cap", which is
    the right reading for a limit whose purpose is to bound a runaway agent.
    """
    argv = ResourceLimits.from_config(ResourcesConfig(memory="2g")).docker_update_argv("c")

    assert "--memory-swap" in argv
    assert argv[argv.index("--memory") + 1] == argv[argv.index("--memory-swap") + 1] == "2g"
    # A cap that names no memory needs no pairing, and must not invent one.
    assert "--memory-swap" not in ResourceLimits.from_config(
        ResourcesConfig(cpus="2", pids=512)
    ).docker_update_argv("c")


# ─── config defaults ────────────────────────────────────────────────────────


def test_container_policy_defaults_are_the_documented_ones() -> None:
    container = GroveConfig().container

    assert container.agent_config.share == "full"
    assert container.egress.mode == "allowlist"
    assert container.egress.allow == ()
    assert container.resources.memory == ""
    assert container.resources.pids == 0
    assert PurePosixPath("/grove/agent-config") == CONTAINER_CONFIG_ROOT


# ─── published CIDR ranges, for hosts whose DNS pin goes stale ──────────────


def _source(url: str = "https://ranges.test/meta") -> RangeSource:
    return RangeSource(url=url, keys=("git", "web"))


def _respond(payload: object, *_a: object, **_k: object) -> httpx.Response:
    """A stubbed `httpx.get`. Bound via `partial` rather than a closure so a
    loop variable cannot leak into it (ruff B023)."""
    return httpx.Response(
        200, json=payload, request=httpx.Request("GET", "https://ranges.test/meta")
    )


def test_published_ranges_keep_only_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    """The v6 filter is load-bearing, not tidiness.

    These payloads carry v6 prefixes; `iptables -d` rejects a v6 address; the
    generated script runs under `set -e`. So one unfiltered v6 CIDR aborts the
    whole firewall and fails the container start. v6 is denied wholesale
    anyway, so nothing is lost.
    """
    payload = {
        "git": ["192.30.252.0/22", "2a0a:a440::/29"],
        "web": ["140.82.112.0/20"],
        "actions": ["13.64.0.0/16"],  # a key not asked for
    }
    monkeypatch.setattr(
        container_policy.httpx,
        "get",
        lambda *a, **k: httpx.Response(
            200, json=payload, request=httpx.Request("GET", "https://ranges.test/meta")
        ),
    )

    ranges = container_policy.fetch_published_ranges(_source())

    assert ranges == ("192.30.252.0/22", "140.82.112.0/20")


@pytest.mark.parametrize(
    "boom",
    [
        httpx.ConnectError("unreachable"),
        httpx.ReadTimeout("slow"),
    ],
)
def test_a_failed_fetch_degrades_to_hostnames_rather_than_failing(
    boom: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort by contract: an optimization must never fail a provision.

    Falling back to `()` leaves exactly the hostname-only allowlist — resolved
    in-container at apply time — so a provider outage costs the improvement,
    never the workspace.
    """

    def _raise(*_a: object, **_k: object) -> httpx.Response:
        raise boom

    monkeypatch.setattr(container_policy.httpx, "get", _raise)

    assert container_policy.fetch_published_ranges(_source()) == ()


def test_a_changed_payload_shape_is_ignored_not_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider is free to change its JSON; Grove is not free to crash on it."""
    for payload in ([1, 2, 3], {"git": "not-a-list"}, {"git": [1, None]}):
        monkeypatch.setattr(
            container_policy.httpx,
            "get",
            functools.partial(_respond, payload),
        )
        assert container_policy.fetch_published_ranges(_source()) == ()


def test_published_ranges_are_additive_to_the_hostname_planes() -> None:
    """The ranges SUPPLEMENT the hostnames, never replace them.

    That is what makes a failed fetch a no-op rather than a narrowing, and it
    costs one resolved `/32` per host to keep.
    """
    policy = EgressPolicy.derive(
        EgressConfig(),
        kind="claude_code",
        fetch_ranges=lambda _s: ("140.82.112.0/20",),
    )

    assert "140.82.112.0/20" in policy.cidrs
    assert "github.com" in policy.hosts  # still resolved in-container as well


def test_no_range_sources_reproduces_the_pre_fix_allowlist() -> None:
    empty = EgressConfig(range_sources=())
    baseline = EgressPolicy.derive(empty, kind="claude_code", fetch_ranges=lambda _s: ())

    assert baseline.cidrs == ()
    assert "github.com" in baseline.hosts


def test_the_default_source_is_githubs_published_ranges() -> None:
    """`github.com` presents ONE A record with a 42-second TTL, so a pin taken
    at container start is a coin flip for the rest of a multi-hour session
    (measured rotating 140.82.116.3 -> 20.29.134.23 inside 80 seconds)."""
    (source,) = EgressConfig().range_sources

    assert source.url == "https://api.github.com/meta"
    # Not `actions`/`packages`/`codespaces`: a workspace does not need GitHub's
    # whole estate to fetch its own repository.
    assert source.keys == ("git", "web", "api")


# ─── never emit a rule for an address nothing can be sent to ────────────────


@pytest.mark.parametrize("value", ["0.0.0.0", "0.0.0.0/32", "0.0.0.0/0", "::", "::/0"])
def test_unspecified_addresses_are_never_allowed(value: str) -> None:
    """Pi-hole and AdGuard answer a blocked name with `0.0.0.0` by DEFAULT, so an
    ordinary allowlist entry on an ad-blocking network becomes a rule that reads
    as an allow and permits nothing."""
    assert EgressPolicy.is_unusable(value) is True
    assert EgressPolicy.is_unusable("140.82.112.0/20") is False
    assert EgressPolicy.is_unusable("github.com") is False


def test_a_config_supplied_unusable_address_is_dropped_from_the_policy() -> None:
    policy = EgressPolicy.derive(
        EgressConfig(allow=("0.0.0.0", "10.1.2.0/24")),
        kind="claude_code",
        fetch_ranges=lambda _s: (),
    )

    assert "0.0.0.0" not in policy.cidrs
    assert "10.1.2.0/24" in policy.cidrs


def test_the_script_skips_an_unusable_address_loudly(tmp_path: Path) -> None:
    """RUNS the generated `allow` helper, because the case that bites arrives at
    APPLY time from the container's own resolver — not from config, where the
    Python-side filter can see it. Loud, because a silently-skipped entry is how
    an operator ends up debugging the wrong layer."""
    bash = shutil.which("bash")
    assert bash is not None
    script = EgressPolicy.derive(
        EgressConfig(), kind="claude_code", fetch_ranges=lambda _s: ()
    ).firewall_script()
    # Take the helper alone, with a stub iptables, so this tests the guard
    # rather than requiring NET_ADMIN.
    helper = script[script.index("allow() {") : script.index("\n}", script.index("allow() {")) + 2]
    harness = tmp_path / "probe.sh"
    harness.write_text(
        f'iptables() {{ echo "RULE $*"; }}\n{helper}\nallow 0.0.0.0\nallow 1.2.3.4\n',
        encoding="utf-8",
    )

    result = subprocess.run([bash, str(harness)], capture_output=True, text=True, check=False)

    assert "skipping unusable address 0.0.0.0" in result.stderr
    assert "RULE -A OUTPUT -d 0.0.0.0" not in result.stdout
    assert "RULE -A OUTPUT -d 1.2.3.4 -j ACCEPT" in result.stdout
