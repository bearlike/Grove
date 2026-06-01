"""On-disk path resolution for Grove.

Wraps `platformdirs` so the rest of the codebase never thinks about
%APPDATA%, ~/Library/Application Support, or $XDG_CONFIG_HOME directly.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir, user_state_dir

_APP_NAME = "grove"
_PROJECT_DIR_NAME = ".grove"
_CONFIG_FILE = "config.json"
_LOCAL_CONFIG_FILE = "config.local.json"
_SCHEMA_FILE = "config.schema.json"
_STATE_FILE = "state.json"
_AUTH_FILE = "auth.json"
_WEBAPP_SESSIONS_FILE = "webapp-sessions.json"
_LOGS_DIR = "logs"
_THEMES_DIR = "themes"


def user_config_path() -> Path:
    """User-scope config file path (XDG / AppData / ~/Library)."""
    return Path(user_config_dir(_APP_NAME)) / _CONFIG_FILE


def user_schema_path() -> Path:
    """Where we write the auto-generated JSON Schema next to the user config."""
    return Path(user_config_dir(_APP_NAME)) / _SCHEMA_FILE


def user_state_path() -> Path:
    """Single global workspace-state file. Keyed internally by repo_root."""
    return Path(user_state_dir(_APP_NAME)) / _STATE_FILE


def init_log_path(workspace_id: str) -> Path:
    """Per-workspace init-script log; lives next to the state file.

    Lets a failed init be diagnosed from the rail (`see {path}`) without
    grepping loguru output. Best-effort — write failures don't block create.
    """
    return Path(user_state_dir(_APP_NAME)) / _LOGS_DIR / f"{workspace_id}-init.log"


def project_config_path(repo_root: Path) -> Path:
    """Committed project config: <repo>/.grove/config.json."""
    return repo_root / _PROJECT_DIR_NAME / _CONFIG_FILE


def project_local_config_path(repo_root: Path) -> Path:
    """Gitignored machine-specific overrides: <repo>/.grove/config.local.json."""
    return repo_root / _PROJECT_DIR_NAME / _LOCAL_CONFIG_FILE


def project_grove_dir(repo_root: Path) -> Path:
    """The `.grove/` directory inside a repo."""
    return repo_root / _PROJECT_DIR_NAME


def user_auth_path() -> Path:
    """Single-file persistent store for pairing challenges + sessions.

    Lives next to the user config so platformdirs places it in the right
    OS-specific directory (Linux: ``~/.config/grove/auth.json``; macOS:
    ``~/Library/Application Support/grove/auth.json``; Windows:
    ``%APPDATA%/grove/auth.json``). Same atomic-write pattern as the
    workspace state store. Holds metadata only — never plaintext tokens
    nor password equivalents.
    """
    return Path(user_config_dir(_APP_NAME)) / _AUTH_FILE


def user_webapp_sessions_path() -> Path:
    """Webapp BFF cookie ↔ daemon-token mapping.

    Persisted server-side so a Next.js process restart doesn't log every
    browser out. Same directory as ``user_auth_path``; daemon process
    never reads this file (different concern, different writer).
    """
    return Path(user_config_dir(_APP_NAME)) / _WEBAPP_SESSIONS_FILE


def user_themes_dir() -> Path:
    """User-scope theme override directory.

    Each `*.toml` file inside is registered as an additional Textual theme
    by `grove.tui.theme.register_themes`. Path-only — never created here;
    the loader treats a missing directory as "no overrides".
    """
    return Path(user_config_dir(_APP_NAME)) / _THEMES_DIR
