"""The one import seam for the optional ``mcp`` SDK, with honest diagnostics.

Three Grove servers defer their SDK import so a host without the ``[mcp]``
extra still gets a clean hint instead of a traceback: ``grove.mcp.server``
(FastMCP), ``grove.core.channel`` and ``grove.core.permission`` (the low-level
``Server``). Each used to wrap ``except ImportError`` around a *deep submodule*
import, which cannot tell "the distribution is absent" from "the distribution
is installed but incompatible" — so when SDK 2.0.0 removed
``mcp.server.fastmcp``, ``grove-mcp`` told an operator to install a package
that was already installed, and the version never came up.

This module lives at the package root deliberately. ``grove.core`` and
``grove.mcp`` both need it, and ``grove.mcp`` may not import ``grove.core``
(the "MCP server speaks only through the client SDK" import-linter contract);
a neutral root module that imports nothing from Grove satisfies both.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module, metadata
from importlib.util import find_spec
from typing import Any, ClassVar


@dataclass(slots=True, frozen=True)
class McpSdk:
    """Deferred imports from the optional ``mcp`` distribution for one consumer.

    ``consumer`` names the Grove surface in every message, so an operator reads
    which server failed rather than a bare import error.
    """

    #: The top-level distribution. Presence is asked about HERE, never about a
    #: submodule — that distinction is the whole point of this class.
    DISTRIBUTION: ClassVar[str] = "mcp"
    #: Mirrors the `mcp` extra in pyproject.toml. Deliberately a constant: the
    #: alternative is hand-parsing a requirement string out of Grove's own
    #: metadata (no `packaging` dependency exists here by policy) plus a
    #: fallback for when that metadata is absent — two failure modes for a
    #: string that only ever appears in an error message.
    SUPPORTED_RANGE: ClassVar[str] = ">=1.2,<2"
    INSTALL_COMMAND: ClassVar[str] = "pip install 'grove[mcp]'"

    consumer: str

    def load(self, module: str, *names: str) -> tuple[Any, ...]:
        """Import ``names`` from ``module``, or raise a diagnosed ``ImportError``.

        A missing symbol (``AttributeError``) is the same failure as a missing
        submodule — an SDK that moved something — so both are diagnosed and
        re-raised as ``ImportError``, which every call site already catches.
        """
        try:
            loaded = import_module(module)
            return tuple(getattr(loaded, name) for name in names)
        except (ImportError, AttributeError) as exc:
            raise ImportError(self.diagnose(exc)) from exc

    def diagnose(self, exc: BaseException) -> str:
        """The message for a failed SDK import — install hint, or version report."""
        if not self.is_installed():
            return f"{self.consumer} requires the MCP SDK — install with: {self.INSTALL_COMMAND}"
        return (
            f"{self.consumer} requires an MCP SDK {self.SUPPORTED_RANGE}, but "
            f"{self.DISTRIBUTION} {self.installed_version() or 'unknown'} is installed — "
            f"reinstall with: {self.INSTALL_COMMAND} ({exc})"
        )

    @classmethod
    def is_installed(cls) -> bool:
        """Whether the distribution is present at all, importable or merely on disk."""
        return cls.installed_version() is not None or find_spec(cls.DISTRIBUTION) is not None

    @classmethod
    def installed_version(cls) -> str | None:
        """The installed distribution's version, or ``None`` when it has no metadata.

        ``None`` does not imply absence — a source checkout on ``sys.path`` is
        importable with no ``dist-info`` — which is why :meth:`is_installed`
        asks a second question rather than reading this one as a boolean.
        """
        try:
            return metadata.version(cls.DISTRIBUTION)
        except metadata.PackageNotFoundError:
            return None
