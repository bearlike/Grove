"""Task-local config-root overrides for filesystem transcript readers.

A workspace can launch an agent with a profile that differs from the daemon's
process environment.  Reads for that workspace need the launched profile, but
changing ``os.environ`` would let concurrent reads see one another's profiles.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar, Token

_config_dir_overrides: ContextVar[dict[str, str] | None] = ContextVar(
    "grove_transcript_config_dir_overrides", default=None
)


def config_dir_override(variable: str) -> str | None:
    """The task-local value for ``variable``, or ``None`` when unscoped.

    An empty string is an explicit override: it clears the process value and
    lets the adapter use its native default root.  ``None`` alone means no
    override and therefore preserves ordinary process-environment discovery.
    """
    return (_config_dir_overrides.get() or {}).get(variable)


@contextlib.contextmanager
def config_dir_scope(variable: str | None, config_dir: str | None) -> Iterator[None]:
    """Apply one config-root override without mutating process environment.

    ``None`` is deliberately a no-op; an empty string remains a real value so
    nested and concurrent readers preserve the old "clear this env var"
    behavior.  ContextVar tokens restore nesting exactly and isolate workers.
    """
    if variable is None or config_dir is None:
        yield
        return
    overrides = dict(_config_dir_overrides.get() or {})
    overrides[variable] = config_dir
    token: Token[dict[str, str] | None] = _config_dir_overrides.set(overrides)
    try:
        yield
    finally:
        _config_dir_overrides.reset(token)
