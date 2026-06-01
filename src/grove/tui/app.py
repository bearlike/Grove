"""GroveApp — the Textual application root."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from grove.core import WorkspaceManager, build
from grove.core.errors import ConfigError
from grove.core.paths import user_themes_dir
from grove.tui.screens.list import WorkspaceListScreen
from grove.tui.screens.pairing import PairingWatcher
from grove.tui.theme import register_themes, resolve_theme_name


class GroveApp(App[None]):
    """Top-level Textual app. Builds a manager and hands it to the list screen."""

    # `.grove-card` is the shared card chrome — bordered, $surface-backed,
    # primary-tinted title. CLAUDE.md's threshold for hoisting was three
    # consumers; we have them now: PeekRail's two cards, the empty banner,
    # and the new WorkspaceList container. Per-widget DEFAULT_CSS keeps
    # only the local additions (e.g. PeekRail #card-pane.-live swaps the
    # border to $primary).
    CSS = """
    Screen { background: $background; }
    Header { background: $primary; color: $foreground; }

    .grove-card {
        background: $surface;
        border: round $secondary;
        border-title-color: $primary;
        border-title-align: left;
        padding: 0 1;
    }
    """

    def __init__(self, manager: WorkspaceManager | None = None) -> None:
        super().__init__()
        self._manager = manager
        self._pairing_watcher: PairingWatcher | None = None

    def on_mount(self) -> None:
        if self._manager is None:
            self._manager = build(Path.cwd())
        register_themes(self, themes_dir=user_themes_dir())
        try:
            self.theme = resolve_theme_name(
                self._manager.config.ui.theme,
                set(self.available_themes),
            )
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        self.push_screen(WorkspaceListScreen(self._manager))
        # Pairing approvals — poll the engine SessionStore once a second
        # and surface a modal whenever a remote browser kicks
        # off a new pair request. The poll is cheap (one read of a small
        # JSON file). Approval is the only secret-handling code path on
        # the TUI side; tokens never reach this UI.
        self._pairing_watcher = PairingWatcher(self)
        self.set_interval(PairingWatcher.POLL_SECONDS, self._pairing_watcher.tick)
