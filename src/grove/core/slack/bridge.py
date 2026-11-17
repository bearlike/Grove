"""The Slack integration as the daemon holds it: one object to build, start and close."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grove.core.errors import EnvSourceError, GroveError
from grove.core.slack.context import SlackContextReader
from grove.core.slack.gateway import SlackGateway
from grove.core.slack.relay import SlackRelay
from grove.core.slack.router import SlackRouter
from grove.core.slack.threads import SlackThreadLog

if TYPE_CHECKING:
    from grove.core.activity import DashboardDelta
    from grove.core.config import SlackConfig
    from grove.core.registry import RepoRegistry


class SlackBridge:
    """The gateway, the inbound router and the outbound relay, wired together.

    Built by :meth:`from_config`, which is the one place config becomes runtime.
    The daemon calls :meth:`start` in its lifespan and :meth:`close` at shutdown,
    exactly like the notification broker.
    """

    def __init__(self, *, gateway: SlackGateway, router: SlackRouter, relay: SlackRelay) -> None:
        """Hold the three parts.

        Args:
            gateway: The Slack connection.
            router: Inbound messages to workspace verbs.
            relay: Agent replies back into threads.
        """
        self._gateway = gateway
        self._router = router
        self._relay = relay

    @classmethod
    def from_config(cls, cfg: SlackConfig, *, registry: RepoRegistry) -> SlackBridge | None:
        """Build the integration, or ``None`` when it is off or has no tokens.

        A missing token is logged, never raised: the daemon must start without
        Slack rather than not start at all.
        """
        if not cfg.enabled:
            return None
        try:
            env = cls._environment(cfg)
        except EnvSourceError as exc:
            logger.error("slack: env source unusable, Slack stays off: {}", exc)
            return None
        bot, app = env.get(cfg.bot_token_env, ""), env.get(cfg.app_token_env, "")
        if not bot or not app:
            missing = [n for n, v in ((cfg.bot_token_env, bot), (cfg.app_token_env, app)) if not v]
            logger.error("slack: enabled but {} not set, Slack stays off", " and ".join(missing))
            return None
        gateway = SlackGateway(bot_token=bot, app_token=app)
        threads = SlackThreadLog()
        reader = SlackContextReader(
            gateway, history=cfg.context_messages, max_attachment_bytes=cfg.max_attachment_bytes
        )
        return cls(
            gateway=gateway,
            router=SlackRouter(
                cfg=cfg, registry=registry, gateway=gateway, threads=threads, reader=reader
            ),
            relay=SlackRelay(
                registry=registry,
                gateway=gateway,
                threads=threads,
                working_reaction=cfg.working_reaction,
            ),
        )

    def start(
        self, subscribe: Callable[[Callable[[DashboardDelta], None]], Callable[[], None]]
    ) -> None:
        """Subscribe the relay to the activity bus and connect to Slack. Blocking.

        A refused token or an unreachable Slack is logged and leaves the relay
        idle; the daemon keeps running.
        """
        self._relay.bind(subscribe)
        try:
            self._gateway.connect(self._router.submit)
        except GroveError as exc:
            logger.error("slack: {}", exc)
            return
        logger.info("slack: connected as {}", self._gateway.bot_user_id)

    def close(self) -> None:
        """Disconnect, stop taking messages, and let queued posts finish."""
        self._gateway.close()
        self._router.close()
        self._relay.close()

    @staticmethod
    def _environment(cfg: SlackConfig) -> dict[str, str]:
        """The daemon's environment, overlaid with the section's env file or command."""
        env = dict(os.environ)
        if cfg.env_file or cfg.env_command:
            from grove.core.env_source import EnvSource  # noqa: PLC0415

            env.update(EnvSource.resolve(cfg, repo_root=Path.home()).values)
        return env


__all__ = ["SlackBridge"]
