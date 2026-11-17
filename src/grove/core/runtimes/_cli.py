"""The provider-CLI subprocess edge every serverless driver shells out through.

A driver speaks to its platform only through the platform's own CLI
(``gcloud``, ``az``, ``aws``, ``faas-cli``), never an SDK, so this is the one
place a driver touches a process. It runs argv and reports what came back; the
driver alone reads meaning into it.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from loguru import logger


@dataclass(frozen=True, slots=True)
class CliResult:
    """One completed provider-CLI invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def detail(self) -> str:
        """The platform's own words for a failure, for an error message."""
        return self.stderr.strip() or self.stdout.strip() or "no output"


class ProviderCli:
    """Runs a provider CLI argv with ``shell=False`` and the driver's credential env."""

    def __init__(self, *, timeout_seconds: float, env: Mapping[str, str] | None = None) -> None:
        """Bind the invocation policy for one driver.

        Args:
            timeout_seconds: How long one call may run before it counts as no answer.
            env: The whole environment the CLI runs with, or ``None`` to inherit
                this process's.
        """
        self._timeout = timeout_seconds
        self._env = dict(env) if env is not None else None

    def call(self, argv: Sequence[str]) -> CliResult | None:
        """Run one command.

        Returns:
            What the CLI answered, a non-zero exit included, or ``None`` when it
            could not be run at all (missing binary, timeout). The two differ:
            a non-zero exit is the platform answering, ``None`` is no answer.
        """
        try:
            done = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=self._timeout,
                env=self._env,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("runtimes: {} could not be invoked: {}", argv[:3], exc)
            return None
        return CliResult(done.returncode, done.stdout, done.stderr)
