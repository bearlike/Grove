"""Grove TUI color language — bearlike-anchored Textual themes.

Two `Theme` constants (`GROVE_DARK`, `GROVE_LIGHT`) define the canonical
palette: warm-dark surfaces with a clay accent, sourced from the
[bearlike/Assistant](https://github.com/bearlike/Assistant) Assistant console
(`the bearlike/Assistant brand palette` + `warm_terracotta.toml`). All hex
values originate here; widgets that need raw hex (Rich `Text` styles,
peek-rail markup) consume `STATUS_HEX` / `INIT_STATUS_HEX` / `REF_HEX`,
keyed by the `dark` flag of the active theme. TCSS consumers reach the
same values via `$status-running`, `$ref`, etc.

Why one source of truth: the Textual `Theme.variables` dict and the Rich
lookup dicts must never disagree. They're both generated from module-level
hex constants here — change a value once and both surfaces follow.

User overrides live as TOML files in `${user_config_dir}/grove/themes/*.toml`
and follow the same shape as bearlike's `warm_terracotta.toml`. Each file
becomes an additional registered theme; partial overrides inherit from the
matching-polarity Grove default (so a one-line file changing `primary` is
enough). See `ThemeOverride` for the schema.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from textual.theme import Theme

from grove.core import InitStatus, WorkspaceStatus
from grove.core.contracts.status_palette import DARK_STATUS_HEX
from grove.core.errors import ConfigError

if TYPE_CHECKING:
    from textual.app import App


# ─── canonical hex values (single source of truth) ─────────────────────────

# Tier model: **inset content wells on an ambient canvas**. The three
# user-facing panels (workspaces, summary, agent) sit DARKER than the
# screen root; chrome bars (footer, status) sit AT the canvas tier; the
# highlighted row LIFTS to the lightest tier above the well. Both modes
# follow the same axis (`$surface` deepest, `$background` middle, `$panel`
# lightest) so the rotation is a 1:1 mental map across themes.
#
# Hex atoms are still the bearlike/Assistant palette — only the tier roles
# they fill have rotated. A captured tmux pane (which carries the agent's
# own dark bg in its SGR cells) blends into the now-darker `$surface`
# rather than floating on a lighter slab.
#
# Dark palette — bearlike/Assistant `:root` (the bearlike/Assistant brand palette).
# Tier hex chosen for *display-robust* contrast (~9:1 WCAG ratio between
# canvas and panel). Previous attempts at ~3 L* and ~8 L* deltas both
# read as "nothing changed" on real-world displays — particularly laptop
# panels with poor low-luminance discrimination. The pair must survive
# bit-depth quantization on cheap displays; that means pushing the panel
# near-black rather than tweaking mid-gray differences.
_DARK_BG: Final = "#2d2d2b"  # canvas tier (Assistant --card hex; root + chrome)
_DARK_SURFACE: Final = "#0e0e0d"  # panel-well tier (near-black; ~9:1 vs canvas)
_DARK_PANEL: Final = "#363633"  # highlight tier (Assistant --accent hex)
_DARK_FG: Final = "#fcfbf9"  # Assistant --foreground
_DARK_FG_MUTED: Final = "#96938c"  # Assistant --muted-foreground
_DARK_PRIMARY: Final = "#d97757"  # brand clay (Assistant --primary)
_DARK_SUCCESS: Final = "#4d9900"  # Assistant --success
_DARK_WARNING: Final = "#b8860b"  # Assistant --agent-3 (yellow-400)
_DARK_ERROR: Final = "#e64c4c"  # Assistant --destructive

_DARK_REF: Final = "#26a69a"  # Assistant --agent-7 (aqua)
_DARK_REF_ADD: Final = "#99d199"  # Assistant --diff-add-text base
_DARK_REF_REMOVE: Final = "#e66666"  # Assistant --code-stderr
_DARK_INFO: Final = "#c2dcf7"  # Assistant --permission

# Light palette — bearlike/Assistant `.light` and warm_terracotta.toml [theme.light].
# Same display-robust contrast goal as dark: panel well must read as a
# distinct surface, not "subtly off-white". Tan brown (~2.6:1 ratio with
# the cream canvas) survives reflection and bit-depth quantization. Pure
# white highlight on top still pops against the deeper tan.
_LIGHT_BG: Final = "#faf9f5"  # canvas tier (warm cream; root + chrome)
_LIGHT_SURFACE: Final = "#a89f86"  # panel-well tier (tan brown; ~2.6:1 vs canvas)
_LIGHT_PANEL: Final = "#ffffff"  # highlight tier (pure white lift)
_LIGHT_FG: Final = "#0a0a0a"
_LIGHT_FG_MUTED: Final = "#858278"
_LIGHT_PRIMARY: Final = "#d97757"
_LIGHT_SUCCESS: Final = "#4a9331"
_LIGHT_WARNING: Final = "#926b00"
_LIGHT_ERROR: Final = "#c03a3a"

_LIGHT_REF: Final = "#1f8c7e"
_LIGHT_REF_REMOVE: Final = "#a83232"
_LIGHT_INFO: Final = "#2f6cd9"

# Status palette — distinct visuals per axis. Defined after the base hex
# atoms so they can compose by reference rather than copy:
#   active   : vibrant lime      (live signal — agent producing output;
#                                 deliberately decoupled from $success
#                                 because the olive-green success hue
#                                 read as muted on dark card backgrounds.
#                                 "Live" deserves a louder green than
#                                 "operation succeeded")
#   idle     : info cyan         (alive but quiet — NOT a warning)
#   offline  : muted gray        (no live signal; distinguished from paused
#                                 by glyph: empty circle vs. pause bars)
#   paused   : muted gray        (deliberate teardown — neutral, NOT amber)
#   orphaned : warning amber     (stranded record — needs cleanup)
#   error    : destructive red   (broken)
# RUNNING is the persisted intent; rarely seen post-reconciliation but
# mapped to the active hex so a debug renderer remains coherent.
# Dark-side status colors live in ``grove.core.contracts.status_palette`` —
# that's the cross-client wire contract every Grove client (TUI today,
# web tomorrow) reads. The TUI's per-status aliases below resolve to those
# canonical values so the rest of this module (Theme.variables, Rich
# lookup dicts) keeps composing by name rather than by literal hex.
_DARK_STATUS_ACTIVE: Final = DARK_STATUS_HEX[WorkspaceStatus.ACTIVE]
_DARK_STATUS_RUNNING: Final = DARK_STATUS_HEX[WorkspaceStatus.RUNNING]
_DARK_STATUS_IDLE: Final = DARK_STATUS_HEX[WorkspaceStatus.IDLE]
_DARK_STATUS_OFFLINE: Final = DARK_STATUS_HEX[WorkspaceStatus.OFFLINE]
_DARK_STATUS_PAUSED: Final = DARK_STATUS_HEX[WorkspaceStatus.PAUSED]
_DARK_STATUS_ORPHANED: Final = DARK_STATUS_HEX[WorkspaceStatus.ORPHANED]
_DARK_STATUS_ERROR: Final = DARK_STATUS_HEX[WorkspaceStatus.ERROR]

_LIGHT_STATUS_ACTIVE: Final = "#65a30d"  # lime-600 — readable on cream
_LIGHT_STATUS_RUNNING: Final = _LIGHT_STATUS_ACTIVE
_LIGHT_STATUS_IDLE: Final = _LIGHT_INFO
_LIGHT_STATUS_OFFLINE: Final = _LIGHT_FG_MUTED
_LIGHT_STATUS_PAUSED: Final = _LIGHT_FG_MUTED
_LIGHT_STATUS_ORPHANED: Final = _LIGHT_WARNING
_LIGHT_STATUS_ERROR: Final = "#a83232"

# Lighter-lime ACTIVE — the "swelled" frame of the live-signal pulse. A
# brighter shade of the rest hue so the eye reads a beat without losing
# the semantic ("still alive"). Distinct enough to register in motion at
# 4 Hz (0.5 s full cycle) on cheap displays, close enough that a single
# static screenshot still reads as "the lime-green one".
_DARK_STATUS_ACTIVE_TINT: Final = "#bef264"  # lime-300 — visible swell delta
_LIGHT_STATUS_ACTIVE_TINT: Final = "#84cc16"  # lime-500 — lighter than rest

# `_LIGHT_REF_ADD` keeps the older deep-green that previously aliased
# `_LIGHT_STATUS_ACTIVE` — diff-add semantics ("a value that grew") read
# better in a deeper, less vivid green than the new "alive" lime.
_LIGHT_REF_ADD: Final = "#3d7a00"


# ─── variables dicts (consumed by Theme.variables → $varname in TCSS) ──────

_DARK_VARS: Final[dict[str, str]] = {
    "status-running": _DARK_STATUS_RUNNING,
    "status-active": _DARK_STATUS_ACTIVE,
    "status-idle": _DARK_STATUS_IDLE,
    "status-offline": _DARK_STATUS_OFFLINE,
    "status-paused": _DARK_STATUS_PAUSED,
    "status-orphaned": _DARK_STATUS_ORPHANED,
    "status-error": _DARK_STATUS_ERROR,
    "ref": _DARK_REF,
    "ref-add": _DARK_REF_ADD,
    "ref-remove": _DARK_REF_REMOVE,
    "info": _DARK_INFO,
}

_LIGHT_VARS: Final[dict[str, str]] = {
    "status-running": _LIGHT_STATUS_RUNNING,
    "status-active": _LIGHT_STATUS_ACTIVE,
    "status-idle": _LIGHT_STATUS_IDLE,
    "status-offline": _LIGHT_STATUS_OFFLINE,
    "status-paused": _LIGHT_STATUS_PAUSED,
    "status-orphaned": _LIGHT_STATUS_ORPHANED,
    "status-error": _LIGHT_STATUS_ERROR,
    "ref": _LIGHT_REF,
    "ref-add": _LIGHT_REF_ADD,
    "ref-remove": _LIGHT_REF_REMOVE,
    "info": _LIGHT_INFO,
}


# ─── Textual Theme objects ─────────────────────────────────────────────────

GROVE_DARK: Final = Theme(
    name="grove-dark",
    primary=_DARK_PRIMARY,
    secondary=_DARK_FG_MUTED,
    accent=_DARK_PRIMARY,
    warning=_DARK_WARNING,
    error=_DARK_ERROR,
    success=_DARK_SUCCESS,
    foreground=_DARK_FG,
    background=_DARK_BG,
    surface=_DARK_SURFACE,
    panel=_DARK_PANEL,
    boost=_DARK_PANEL,
    dark=True,
    variables=dict(_DARK_VARS),
)

GROVE_LIGHT: Final = Theme(
    name="grove-light",
    primary=_LIGHT_PRIMARY,
    secondary=_LIGHT_FG_MUTED,
    accent=_LIGHT_PRIMARY,
    warning=_LIGHT_WARNING,
    error=_LIGHT_ERROR,
    success=_LIGHT_SUCCESS,
    foreground=_LIGHT_FG,
    background=_LIGHT_BG,
    surface=_LIGHT_SURFACE,
    panel=_LIGHT_PANEL,
    boost=_LIGHT_PANEL,
    dark=False,
    variables=dict(_LIGHT_VARS),
)


# ─── lookup tables for Rich-side consumers ─────────────────────────────────

# Keyed by the active theme's `dark` flag. Widgets that emit Rich markup or
# `Text(style=...)` look up hex here — Rich does not understand `$varname`.
STATUS_HEX: Final[dict[bool, dict[WorkspaceStatus, str]]] = {
    # Dark side comes from the canonical wire contract — other clients read
    # the same dict directly. The light side stays TUI-only (no other client
    # currently supports light mode), so it's defined inline here.
    True: dict(DARK_STATUS_HEX),
    False: {
        WorkspaceStatus.RUNNING: _LIGHT_STATUS_RUNNING,
        WorkspaceStatus.ACTIVE: _LIGHT_STATUS_ACTIVE,
        WorkspaceStatus.IDLE: _LIGHT_STATUS_IDLE,
        WorkspaceStatus.OFFLINE: _LIGHT_STATUS_OFFLINE,
        WorkspaceStatus.PAUSED: _LIGHT_STATUS_PAUSED,
        WorkspaceStatus.ORPHANED: _LIGHT_STATUS_ORPHANED,
        WorkspaceStatus.ERROR: _LIGHT_STATUS_ERROR,
    },
}

# Theme-keyed hex for the ACTIVE pulse's "swelled" frame. Frame 0 of the
# pulse reuses ``STATUS_HEX[dark][ACTIVE]`` (single source of truth);
# frame 1 reaches here. Same dark/light shape as the other Rich-side
# lookup dicts so consumers stay on one accessor pattern.
ACTIVE_PULSE_TINT_HEX: Final[dict[bool, str]] = {
    True: _DARK_STATUS_ACTIVE_TINT,
    False: _LIGHT_STATUS_ACTIVE_TINT,
}

# `init_status` colors. OK and SKIPPED are not currently rendered (text-only),
# but exposing them here keeps the contract complete and makes hookup a
# one-liner the day the rail wants to color them.
INIT_STATUS_HEX: Final[dict[bool, dict[InitStatus, str]]] = {
    True: {
        InitStatus.OK: _DARK_REF_ADD,
        InitStatus.FAILED: _DARK_STATUS_ERROR,
        InitStatus.SKIPPED: _DARK_FG_MUTED,
    },
    False: {
        InitStatus.OK: _LIGHT_REF_ADD,
        InitStatus.FAILED: _LIGHT_STATUS_ERROR,
        InitStatus.SKIPPED: _LIGHT_FG_MUTED,
    },
}

RefKind = Literal["branch", "diff_add", "diff_remove", "info"]

REF_HEX: Final[dict[bool, dict[RefKind, str]]] = {
    True: {
        "branch": _DARK_REF,
        "diff_add": _DARK_REF_ADD,
        "diff_remove": _DARK_REF_REMOVE,
        "info": _DARK_INFO,
    },
    False: {
        "branch": _LIGHT_REF,
        "diff_add": _LIGHT_REF_ADD,
        "diff_remove": _LIGHT_REF_REMOVE,
        "info": _LIGHT_INFO,
    },
}

ChromeKind = Literal["accent", "muted"]

# Footer + chrome accents in Rich markup. Same dark/polarity shape as the
# other Rich-side lookup dicts so consumers reach colors via a single
# accessor pattern — see `_status.chrome_color`.
CHROME_HEX: Final[dict[bool, dict[ChromeKind, str]]] = {
    True: {
        "accent": _DARK_PRIMARY,
        "muted": _DARK_FG_MUTED,
    },
    False: {
        "accent": _LIGHT_PRIMARY,
        "muted": _LIGHT_FG_MUTED,
    },
}


# ─── TOML override schema ──────────────────────────────────────────────────


_FROZEN = ConfigDict(extra="forbid", validate_default=True, frozen=True)


class ThemeColors(BaseModel):
    """Optional Textual color slots; missing → fall back to base theme."""

    model_config = _FROZEN

    primary: str | None = None
    secondary: str | None = None
    accent: str | None = None
    foreground: str | None = None
    background: str | None = None
    surface: str | None = None
    panel: str | None = None
    success: str | None = None
    warning: str | None = None
    error: str | None = None


class ThemeOverride(BaseModel):
    """User TOML override for a Grove theme.

    File shape (one theme per `*.toml` under `${user_config_dir}/grove/themes/`)::

        name = "midnight-clay"
        dark = true                     # required: gates which Grove default
                                        # supplies the missing fields.

        [colors]                        # optional; any subset of slots.
        primary = "#..."
        background = "#..."

        [variables]                     # optional; merges over the base theme's
        status-running = "#..."         # custom variables.

    Unknown top-level keys or unknown `[colors]` slots raise `ConfigError`.
    """

    model_config = _FROZEN

    name: str = Field(min_length=1)
    dark: bool
    colors: ThemeColors = Field(default_factory=ThemeColors)
    variables: dict[str, str] = Field(default_factory=dict)


# ─── registration + resolution ─────────────────────────────────────────────


def register_themes(app: App[Any], *, themes_dir: Path | None = None) -> None:
    """Register Grove's built-in themes onto `app`, plus any TOML overrides.

    `themes_dir` is treated as best-effort: a non-existent or empty directory
    is a no-op. Malformed files raise `ConfigError` at registration time
    rather than at theme-switch time so the failure surfaces close to the
    cause. Idempotent on the built-ins; user themes register on first sight.
    """
    for theme in (GROVE_DARK, GROVE_LIGHT):
        if theme.name not in app.available_themes:
            app.register_theme(theme)
    if themes_dir is None:
        return
    for theme in load_theme_overrides(themes_dir):
        if theme.name not in app.available_themes:
            app.register_theme(theme)


def load_theme_overrides(themes_dir: Path) -> list[Theme]:
    """Read every `*.toml` in `themes_dir` and return Textual `Theme` objects.

    Returns an empty list if the directory does not exist. Files are
    processed in sorted order so registration is deterministic. Each
    override merges with `GROVE_DARK` or `GROVE_LIGHT` based on its
    `dark` flag — fields the user did not set inherit from the base.
    """
    if not themes_dir.is_dir():
        return []
    themes: list[Theme] = []
    for toml_path in sorted(themes_dir.glob("*.toml")):
        try:
            with toml_path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read theme {toml_path}: {exc}") from exc
        try:
            override = ThemeOverride.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(f"invalid theme {toml_path}: {exc}") from exc
        themes.append(_apply_override(override))
    return themes


def _apply_override(override: ThemeOverride) -> Theme:
    """Materialize an override into a full `Theme` against its polarity base."""
    base = GROVE_DARK if override.dark else GROVE_LIGHT
    cols = override.colors
    merged_vars = {**base.variables, **override.variables}
    return Theme(
        name=override.name,
        primary=cols.primary or base.primary,
        secondary=cols.secondary or base.secondary,
        accent=cols.accent or base.accent,
        warning=cols.warning or base.warning,
        error=cols.error or base.error,
        success=cols.success or base.success,
        foreground=cols.foreground or base.foreground,
        background=cols.background or base.background,
        surface=cols.surface or base.surface,
        panel=cols.panel or base.panel,
        boost=base.boost,
        dark=override.dark,
        variables=merged_vars,
    )


def resolve_theme_name(setting: str, available: set[str]) -> str:
    """Map a `ui.theme` config value to a registered theme name.

    `auto` and `dark` resolve to `grove-dark`; `light` to `grove-light`.
    Any other string is treated as a custom theme id (e.g. registered from a
    user TOML override) and returned verbatim if present in `available`.

    Raises:
        ValueError: if `setting` is a custom name that has not been registered.
    """
    if setting in {"auto", "dark"}:
        return GROVE_DARK.name
    if setting == "light":
        return GROVE_LIGHT.name
    if setting in available:
        return setting
    raise ValueError(f"unknown ui.theme {setting!r}; registered themes: {sorted(available)}")
