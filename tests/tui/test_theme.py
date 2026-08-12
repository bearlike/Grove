"""Pin the bearlike-anchored color contract.

These tests assert *roles*, not literal pixel values past the canonical
brand hex — the hex constants live in `grove.tui.theme` and tests should
catch *semantic* drift (paused == warning, stale == paused, primary not
clay, etc.), not micro-tone tuning. Where a literal hex is asserted it's
because that hex is the bearlike anchor we explicitly committed to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core import InitStatus, WorkspaceStatus
from grove.core.config import GroveConfig
from grove.core.contracts.phase_palette import DARK_BLOCKED_HEX, DARK_PHASE_HEX
from grove.core.contracts.runtime_palette import DARK_RUNTIME_HEX
from grove.core.errors import ConfigError
from grove.core.manager import WorkspaceManager
from grove.core.phase import PHASE_ORDER
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime
from grove.tui._status import (
    blocked_color,
    init_status_color,
    phase_color,
    ref_color,
    runtime_color,
    status_color,
    status_glyph,
)
from grove.tui.app import GroveApp
from grove.tui.theme import (
    ACTIVE_PULSE_TINT_HEX,
    BLOCKED_HEX,
    GROVE_DARK,
    GROVE_LIGHT,
    PHASE_HEX,
    RUNTIME_HEX,
    STATUS_HEX,
    load_theme_overrides,
    resolve_theme_name,
)
from tests.conftest import FakeTmux

# ─── pure palette contract ───────────────────────────────────────────────────


def test_status_axes_have_distinct_colors_in_both_modes() -> None:
    """The whole point of the spec: each *informational* status reads as a
    different color so the table works at a glance.

    PAUSED and OFFLINE intentionally share a hex (both are "no live signal,
    not a warning") — they're distinguished by glyph (‖ vs ○), not color.
    """
    for dark in (True, False):
        active = status_color(WorkspaceStatus.ACTIVE, dark=dark)
        idle = status_color(WorkspaceStatus.IDLE, dark=dark)
        paused = status_color(WorkspaceStatus.PAUSED, dark=dark)
        orphaned = status_color(WorkspaceStatus.ORPHANED, dark=dark)
        error = status_color(WorkspaceStatus.ERROR, dark=dark)
        assert len({active, idle, paused, orphaned, error}) == 5, (
            f"the five informational statuses must be visually distinct (dark={dark})"
        )


def test_paused_is_neutral_not_amber() -> None:
    """Regression guard: previous palette painted PAUSED yellow, which read
    as a warning. The fix is gray (neutral), and it must stay gray —
    distinct from the warning amber used for ORPHANED."""
    for dark in (True, False):
        paused = status_color(WorkspaceStatus.PAUSED, dark=dark)
        orphaned = status_color(WorkspaceStatus.ORPHANED, dark=dark)
        assert paused != orphaned, f"paused must NOT share orphaned amber (dark={dark})"


def test_idle_is_distinct_from_active_and_offline() -> None:
    """Idle must read as 'alive but quiet' — not the same green as active
    and not the same gray as offline. Activity is a continuum, not a binary."""
    for dark in (True, False):
        active = status_color(WorkspaceStatus.ACTIVE, dark=dark)
        idle = status_color(WorkspaceStatus.IDLE, dark=dark)
        offline = status_color(WorkspaceStatus.OFFLINE, dark=dark)
        assert idle != active, f"idle must not look like active (dark={dark})"
        assert idle != offline, f"idle must not look like offline (dark={dark})"


def test_active_pulse_tint_distinct_from_base_active_in_both_modes() -> None:
    """The mint-tinted swelled frame of the live-signal pulse must not collapse
    onto the resting-frame hex in either polarity. A future palette tweak that
    accidentally equates them would degrade the pulse to a glyph-only swap
    with no color motion — and the design system mandates both axes move."""
    for dark in (True, False):
        rest = STATUS_HEX[dark][WorkspaceStatus.ACTIVE]
        swell = ACTIVE_PULSE_TINT_HEX[dark]
        assert rest != swell, f"pulse tint collapsed onto base active (dark={dark}): {rest}"


def test_canonical_brand_clay_pinned() -> None:
    """Clay `#d97757` is the bearlike brand anchor — both modes share it."""
    assert GROVE_DARK.primary.lower() == "#d97757"
    assert GROVE_LIGHT.primary.lower() == "#d97757"


def test_inset_well_tier_ordering() -> None:
    """Both modes follow the inset-well tier model: panels (`$surface`) sit
    DARKER than the canvas (`$background`); the highlighted row (`$panel`)
    LIFTS to the lightest tier above the well. Pin the canvas hex (still
    a bearlike-anchored atom) and the relative ordering across tiers."""
    assert GROVE_DARK.background == "#2d2d2b"  # canvas (Assistant --card)
    assert GROVE_DARK.surface == "#0e0e0d"  # panel well (~9:1 vs canvas)
    assert GROVE_DARK.panel == "#363633"  # highlight (Assistant --accent)
    assert GROVE_LIGHT.background == "#faf9f5"  # canvas (warm cream)
    assert GROVE_LIGHT.surface == "#a89f86"  # panel well (~2.6:1 vs canvas)
    assert GROVE_LIGHT.panel == "#ffffff"  # highlight
    assert GROVE_DARK.dark is True
    assert GROVE_LIGHT.dark is False


def test_status_glyphs_distinct_per_user_visible_status() -> None:
    """Every user-visible status renders with a unique glyph. RUNNING is the
    persisted intent (never user-visible after reconciliation) and shares the
    ACTIVE glyph by design — only the five computed statuses + paused need
    to look distinct in the table."""
    visible = (
        WorkspaceStatus.ACTIVE,
        WorkspaceStatus.IDLE,
        WorkspaceStatus.OFFLINE,
        WorkspaceStatus.PAUSED,
        WorkspaceStatus.ORPHANED,
        WorkspaceStatus.ERROR,
    )
    glyphs = {status_glyph(s) for s in visible}
    assert len(glyphs) == len(visible)


def test_init_status_failed_uses_error_palette() -> None:
    for dark in (True, False):
        assert init_status_color(InitStatus.FAILED, dark=dark) == status_color(
            WorkspaceStatus.ERROR, dark=dark
        )


def test_ref_kinds_resolve_to_unique_hex_per_mode() -> None:
    for dark in (True, False):
        kinds = {ref_color(k, dark=dark) for k in ("branch", "diff_add", "diff_remove", "info")}
        assert len(kinds) == 4


# ─── task-phase palette (third axis: contracts.phase_palette cross-client) ───


def test_tui_dark_phase_hex_matches_the_cross_client_contract() -> None:
    """`theme.PHASE_HEX[True]` must be sourced verbatim from
    `contracts.phase_palette.DARK_PHASE_HEX` — the same "TUI↔web can't drift
    by construction" guarantee the agent-state palette already has. This
    pins the import, not a coincidence of matching literals."""
    assert PHASE_HEX[True] == dict(DARK_PHASE_HEX)
    for phase in PHASE_ORDER:
        assert phase_color(phase, dark=True) == DARK_PHASE_HEX[phase]


def test_phase_ramp_is_distinct_per_phase_in_both_modes() -> None:
    """All six phases must be visually distinguishable — the whole point of
    a sequential ramp is that each step reads as a different point on it."""
    for dark in (True, False):
        hexes = {phase_color(p, dark=dark) for p in PHASE_ORDER}
        assert len(hexes) == len(PHASE_ORDER), f"phase ramp has a hex collision (dark={dark})"


def test_phase_done_leaves_the_ramp_for_muted_gray() -> None:
    """`done` is deliberately NOT a deeper lime — it recedes to the same
    muted-gray atom IDLE/OFFLINE use, because a converged task is settled
    rather than intense (contracts.phase_palette's own rationale)."""
    for dark in (True, False):
        done_hex = phase_color("done", dark=dark)
        offline_hex = status_color(WorkspaceStatus.OFFLINE, dark=dark)
        assert done_hex == offline_hex, f"done should reuse the muted-gray atom (dark={dark})"


# ─── blocked flag (a FLAG beside a phase claim, not a TaskPhase member) ──────


def test_tui_dark_blocked_hex_matches_the_cross_client_contract() -> None:
    """`theme.BLOCKED_HEX[True]` must be sourced verbatim from
    `contracts.phase_palette.DARK_BLOCKED_HEX` — the same "TUI↔web can't
    drift by construction" guarantee `PHASE_HEX`'s dark side already has."""
    assert BLOCKED_HEX[True] == DARK_BLOCKED_HEX
    assert blocked_color(dark=True) == DARK_BLOCKED_HEX


def test_blocked_is_not_a_phase_ramp_member() -> None:
    """`blocked` co-occurs with any phase — it is a flag beside the claim, not
    a phase in its own right (`grove.core.phase.TaskPhase` deliberately
    excludes it) — so it must never be wedged into `PHASE_HEX` as a seventh
    ramp entry, only its own single-color `BLOCKED_HEX` slot."""
    for dark in (True, False):
        assert set(PHASE_HEX[dark]) == set(PHASE_ORDER)
        assert "blocked" not in PHASE_HEX[dark]


def test_blocked_color_light_side_reuses_the_warning_amber_atom() -> None:
    """The light side is TUI-only (mirrors `_LIGHT_PHASE_DONE` reusing the
    muted-gray atom) — it composes the existing warning amber rather than
    minting a new hue, the same "wants the human" semantic `WorkspaceStatus.
    ORPHANED` already carries. Only the light side is pinned to a specific
    atom here: the dark side is the cross-client wire contract and may pick
    its own exact hex, verified above only for byte-identity with the
    contract, not for which existing atom it happens to equal."""
    assert blocked_color(dark=False) == status_color(WorkspaceStatus.ORPHANED, dark=False)


# ─── theme name resolution ───────────────────────────────────────────────────


def test_resolve_auto_and_dark_map_to_grove_dark() -> None:
    available = {GROVE_DARK.name, GROVE_LIGHT.name}
    assert resolve_theme_name("auto", available) == GROVE_DARK.name
    assert resolve_theme_name("dark", available) == GROVE_DARK.name


def test_resolve_light_maps_to_grove_light() -> None:
    available = {GROVE_DARK.name, GROVE_LIGHT.name}
    assert resolve_theme_name("light", available) == GROVE_LIGHT.name


def test_resolve_unknown_custom_theme_raises() -> None:
    with pytest.raises(ValueError, match=r"unknown ui\.theme"):
        resolve_theme_name("does-not-exist", {GROVE_DARK.name, GROVE_LIGHT.name})


def test_resolve_known_custom_theme_passthrough() -> None:
    available = {GROVE_DARK.name, GROVE_LIGHT.name, "midnight-clay"}
    assert resolve_theme_name("midnight-clay", available) == "midnight-clay"


# ─── live wiring ─────────────────────────────────────────────────────────────


def _manager(tmp_repo: Path, tmp_path: Path, *, theme: str = "auto") -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            "tmux": {"session_prefix": "test-"},
            "ui": {"theme": theme},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


@pytest.mark.asyncio
async def test_app_registers_grove_themes(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert "grove-dark" in app.available_themes
        assert "grove-light" in app.available_themes
        assert app.theme == "grove-dark"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_app_selects_light_theme_when_configured(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    app = GroveApp(_manager(tmp_repo, tmp_path, theme="light"))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app.theme == "grove-light"
        assert app.current_theme.dark is False
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_app_raises_config_error_on_unknown_theme(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    app = GroveApp(_manager(tmp_repo, tmp_path, theme="midnight-clay"))
    with pytest.raises(ConfigError):
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()


# ─── TOML overrides ─────────────────────────────────────────────────────────


def _write_toml(themes_dir: Path, name: str, body: str) -> Path:
    themes_dir.mkdir(parents=True, exist_ok=True)
    path = themes_dir / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_overrides_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    """Best-effort contract: missing dir → no overrides, no exception."""
    assert load_theme_overrides(tmp_path / "no-such") == []


def test_load_overrides_partial_inherits_from_polarity_base(tmp_path: Path) -> None:
    """A one-line override should inherit every other field from the matching
    Grove default — that's the affordance the cascade rule promises."""
    _write_toml(
        tmp_path,
        "midnight-clay",
        """
        name = "midnight-clay"
        dark = true
        [colors]
        primary = "#ff00ff"
        """,
    )
    [theme] = load_theme_overrides(tmp_path)
    assert theme.name == "midnight-clay"
    assert theme.primary == "#ff00ff"
    # Inherited from GROVE_DARK:
    assert theme.background == GROVE_DARK.background
    assert theme.foreground == GROVE_DARK.foreground
    assert theme.dark is True
    # Variables inherit too unless overridden.
    assert theme.variables["status-running"] == GROVE_DARK.variables["status-running"]


def test_load_overrides_variables_merge_over_base(tmp_path: Path) -> None:
    _write_toml(
        tmp_path,
        "subtle",
        """
        name = "subtle"
        dark = false
        [variables]
        ref = "#000000"
        """,
    )
    [theme] = load_theme_overrides(tmp_path)
    assert theme.variables["ref"] == "#000000"
    # Untouched variables still inherit from GROVE_LIGHT.
    assert theme.variables["status-error"] == GROVE_LIGHT.variables["status-error"]


def test_load_overrides_unknown_field_raises_config_error(tmp_path: Path) -> None:
    _write_toml(
        tmp_path,
        "broken",
        """
        name = "broken"
        dark = true
        bogus = "value"
        """,
    )
    with pytest.raises(ConfigError, match="invalid theme"):
        load_theme_overrides(tmp_path)


def test_load_overrides_missing_required_raises_config_error(tmp_path: Path) -> None:
    _write_toml(
        tmp_path,
        "missing",
        """
        dark = true
        """,
    )
    with pytest.raises(ConfigError, match="invalid theme"):
        load_theme_overrides(tmp_path)


def test_load_overrides_malformed_toml_raises_config_error(tmp_path: Path) -> None:
    _write_toml(tmp_path, "bad", "this is not = valid toml = bad\n")
    with pytest.raises(ConfigError, match="cannot read theme"):
        load_theme_overrides(tmp_path)


def test_load_overrides_orders_files_deterministically(tmp_path: Path) -> None:
    _write_toml(
        tmp_path,
        "alpha",
        """name = "alpha"\ndark = true\n""",
    )
    _write_toml(
        tmp_path,
        "beta",
        """name = "beta"\ndark = false\n""",
    )
    names = [t.name for t in load_theme_overrides(tmp_path)]
    assert names == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_app_registers_user_overrides(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: dropping a TOML in the configured themes dir makes it
    selectable via `ui.theme = "<name>"`."""
    del fake_tmux
    themes_dir = tmp_path / "themes"
    _write_toml(
        themes_dir,
        "midnight-clay",
        """
        name = "midnight-clay"
        dark = true
        [colors]
        primary = "#112233"
        """,
    )
    monkeypatch.setattr("grove.tui.app.user_themes_dir", lambda: themes_dir)

    app = GroveApp(_manager(tmp_repo, tmp_path, theme="midnight-clay"))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert "midnight-clay" in app.available_themes
        assert app.theme == "midnight-clay"
        assert app.current_theme.primary == "#112233"
        # Inheritance check end-to-end: bg comes from GROVE_DARK.
        assert app.current_theme.background == GROVE_DARK.background
        await pilot.press("q")
        await pilot.pause()


def test_tui_dark_runtime_hex_matches_the_cross_client_contract() -> None:
    """`theme.RUNTIME_HEX[True]` must be sourced verbatim from
    `contracts.runtime_palette.DARK_RUNTIME_HEX` — the isolation axis gets the
    same "TUI↔web can't drift by construction" guarantee as the other three."""
    assert RUNTIME_HEX[True] == dict(DARK_RUNTIME_HEX)
    for runtime in Runtime:
        assert runtime_color(runtime, dark=True) == DARK_RUNTIME_HEX[runtime]


def test_runtime_marks_are_distinct_in_both_modes() -> None:
    """Host and container must be tellable apart by color as well as glyph, in
    both polarities — a two-member axis whose members share a hue would leave
    the glyph carrying the whole signal on a monochrome-ish terminal."""
    for dark in (True, False):
        assert runtime_color(Runtime.HOST, dark=dark) != runtime_color(Runtime.CONTAINER, dark=dark)


def test_provisioning_is_painted_in_both_modes_and_never_the_dead_gray() -> None:
    """The defect this status was added for is a user reading gray as dead and
    reaching for a destructive verb, so the one thing its hue must never be is
    the muted gray OFFLINE and PAUSED share. It borrows IDLE's info hue in both
    polarities — alive, not yet ready — rather than taking a colour of its own.

    Both polarities are asserted because a status added to the dark wire
    contract renders BLANK-ish on the TUI's light side until `STATUS_HEX[False]`
    learns it, and nothing else in the suite would notice.
    """
    for dark in (True, False):
        provisioning = status_color(WorkspaceStatus.PROVISIONING, dark=dark)
        assert provisioning == status_color(WorkspaceStatus.IDLE, dark=dark)
        assert provisioning != status_color(WorkspaceStatus.OFFLINE, dark=dark)
        assert provisioning != status_color(WorkspaceStatus.PAUSED, dark=dark)


def test_every_workspace_status_resolves_a_hex_in_both_modes() -> None:
    """`status_color` falls back to plain white/black for an unmapped member,
    which is not a crash — it is a status silently rendering as undifferentiated
    text. Enumerate the enum so the next status added to the wire contract
    cannot reach the TUI half-painted."""
    for dark in (True, False):
        for status in WorkspaceStatus:
            assert status in STATUS_HEX[dark], f"{status} unmapped (dark={dark})"
