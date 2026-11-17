"""Read a runtime block's ``*_env`` variable, or refuse by name before any I/O.

A runtime block names where its credentials and endpoints live (``url_env``,
``password_env``) and never holds them, so the value is read at the moment of
use. An unset variable used to read as ``""``: the URL became a relative path
and the error that surfaced was httpx's own, naming no variable, or a password
was sent empty and the server's 401 named no cause (#1049). The rule here is
the one ``EnvReferences`` states for a ``${VAR}`` reference: naming a variable
is the opt-in, so unset or empty is an error that names the variable and the
dotted config path. An EMPTY field name means the feature is off, and nothing
is read.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from grove.core.errors import ConfigError


class RuntimeEnv:
    """The ``*_env`` reads of one ``runtimes.<name>`` block."""

    def __init__(self, block: str, environ: Mapping[str, str] | None = None) -> None:
        """Bind to one block.

        Args:
            block: the ``runtimes.<name>`` key, for the dotted path in a refusal.
            environ: the environment to read; this process's own when omitted.
        """
        self._block = block
        self._environ = os.environ if environ is None else environ

    def require(self, field: str, variable: str) -> str:
        """The value of ``variable``, which the block's ``field`` names.

        Args:
            field: the block's field holding the variable name, such as
                ``url_env``, for the dotted path.
            variable: the variable name that field holds.

        Raises:
            ConfigError: the field is empty, or the variable it names is unset
                or empty in this process's environment.
        """
        path = f"runtimes.{self._block}.{field}"
        if not variable:
            raise ConfigError(f"{path} is empty, but this runtime needs the variable it names")
        value = self._environ.get(variable, "")
        if not value:
            raise ConfigError(
                f"{path} names {variable}, which is not set in this process's environment"
            )
        return value

    def optional(self, field: str, variable: str) -> str:
        """The value of ``variable`` when ``field`` names one, else ``""`` (the feature is off).

        Raises:
            ConfigError: ``field`` names a variable that is unset or empty: naming
                it is the opt-in, so a missing value is a mistake, not "off".
        """
        return self.require(field, variable) if variable else ""


__all__ = ["RuntimeEnv"]
