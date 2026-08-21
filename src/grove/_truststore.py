"""Route this process's TLS verification through the OS trust store.

A virtualenv installs upstream ``certifi``, whose ``cacert.pem`` carries the
public roots and nothing else. The system store carries those roots *plus*
whatever the machine has been told to trust — a corporate CA, an internal PKI,
a homelab issuer. So on any host with a private CA, ``urllib`` succeeds and
``httpx`` fails **against the same URL in the same shell**, which is what makes
this read as a bug in whatever code happened to use ``httpx``.

Grove is the shape that suffers most: it is a host-side tool whose whole job is
talking to a person's own infrastructure — their Gitea, their gateway, their
collector — and those are exactly the endpoints a public root bundle cannot
verify. The symptom is never "TLS failed": ticket enrichment renders bare ids
because a bare ``TicketRef`` is the legitimate persisted shape, so a
certificate problem presents as *these tickets have no titles*.

**The fix is a process-level trust decision, not a per-client argument.** Grove
builds ``httpx`` clients at a dozen sites (tickets, quota, notifications, the
client SDK, telemetry backfill, mewbo) and will build more; threading a
``verify=`` through each is a rule that a thirteenth site silently does not
have. :func:`use_system_trust_store` is called once per entry point and every
present and future client inherits it, including those inside dependencies.

``truststore`` is the ecosystem's answer to this and is what pip itself uses;
it is pure Python with no dependencies of its own. The import is still
defensive, in the ``bashlex`` idiom: an installation that predates this
dependency keeps working with the old behaviour rather than failing to start.

This module lives at the package root for the same reason as
:mod:`grove._mcp_sdk` — ``grove.mcp`` may not import ``grove.core``, and both
need it, so it imports nothing from Grove.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

DISABLE_ENV: Final = "GROVE_NATIVE_TLS"
"""Set to ``0``/``false``/``no`` to keep the interpreter's default bundle.

An env var rather than a config field, deliberately. ``grove.mcp`` may not read
:mod:`grove.core.config` at all, and the decision has to be made before the
first HTTPS call — which, for the daemon, precedes the config cascade of any
particular repo. It is an escape hatch for a host whose system store is the
*broken* one, never a knob anybody is expected to reach for.
"""

CA_PATH_ENV: Final = "GROVE_TLS_CA_PATH"
"""File or directory containing extra CA roots for outbound TLS verification.

Unlike :data:`DISABLE_ENV`, this is a trust ADDITION: its roots join both the
operating system store and the public roots HTTPX supplies through certifi.
"""


class TrustStoreError(ValueError):
    """A configured additional CA path cannot be loaded safely."""


_FALSE: Final = frozenset({"0", "false", "no", "off"})


@dataclass(slots=True)
class _AdditionalCA:
    path: Path | None = None
    context_type: type[object] | None = None


_ADDITIONAL_CA = _AdditionalCA()


def _validated_ca_path(raw: str | Path | None) -> Path | None:
    """Return a readable configured CA file or directory, or fail before I/O starts."""
    if raw is None or not str(raw).strip():
        return None
    path = Path(raw).expanduser()
    try:
        if path.is_file():
            with path.open("rb"):
                pass
        elif path.is_dir():
            next(path.iterdir(), None)
        else:
            raise TrustStoreError(
                f"{CA_PATH_ENV} names {path}, which is neither a CA file nor a CA directory"
            )
    except OSError as exc:
        raise TrustStoreError(f"{CA_PATH_ENV} names an unreadable path {path}: {exc}") from exc
    return path


def _install_additional_ca_loader() -> None:
    """Make truststore-created contexts add the configured root after their own roots.

    ``truststore.inject_into_ssl()`` replaces ``ssl.SSLContext`` but clients such
    as HTTPX create their contexts later. Wrapping the injected class's public
    ``load_verify_locations`` is therefore the process-wide seam: HTTPX first
    loads certifi, this wrapper adds the deployment CA, and truststore adds the
    operating system roots during the TLS handshake.
    """
    context_type = ssl.SSLContext
    if _ADDITIONAL_CA.context_type is context_type:
        return
    original = context_type.load_verify_locations

    def load_verify_locations(
        context: ssl.SSLContext,
        cafile: str | bytes | os.PathLike[str] | os.PathLike[bytes] | None = None,
        capath: str | bytes | os.PathLike[str] | os.PathLike[bytes] | None = None,
        cadata: str | bytes | None = None,
    ) -> None:
        original(context, cafile=cafile, capath=capath, cadata=cast("Any", cadata))
        extra = _ADDITIONAL_CA.path
        if extra is not None:
            original(
                context,
                cafile=str(extra) if extra.is_file() else None,
                capath=str(extra) if extra.is_dir() else None,
            )

    try:
        setattr(context_type, "load_verify_locations", load_verify_locations)  # noqa: B010
    except (AttributeError, TypeError) as exc:
        raise TrustStoreError(
            "the configured CA requires truststore's injected SSL context"
        ) from exc
    _ADDITIONAL_CA.context_type = context_type


def use_system_trust_store(ca_path: str | Path | None = None) -> str | None:
    """Make TLS clients trust OS roots plus an optional deployment CA file or directory.

    The additional path is never a replacement bundle: contexts retain HTTPX's
    certifi roots, receive the configured CA through ``load_verify_locations``,
    and truststore supplies the operating system roots. A named path that cannot
    be read raises :class:`TrustStoreError`; silently falling back would make a
    broken private PKI deployment look configured.

    Returns ``None`` when the system store is now in use, or a one-line reason
    when no additional CA was named and native trust is unavailable. Idempotent
    for a fixed configured path.
    """
    extra = _validated_ca_path(ca_path)
    _ADDITIONAL_CA.path = extra
    disabled = os.environ.get(DISABLE_ENV)
    if disabled is not None and disabled.strip().lower() in _FALSE:
        if extra is not None:
            raise TrustStoreError(
                f"{CA_PATH_ENV} cannot be used while {DISABLE_ENV} disables truststore"
            )
        return f"disabled by {DISABLE_ENV}"
    try:
        # Deferred deliberately: an installation predating this dependency must
        # still start, and this runs once per process rather than per call.
        import truststore  # noqa: PLC0415
    except ImportError:
        if extra is not None:
            raise TrustStoreError(f"{CA_PATH_ENV} requires the truststore package") from None
        return "truststore is not installed"
    try:
        truststore.inject_into_ssl()
        if extra is not None:
            _install_additional_ca_loader()
    except TrustStoreError:
        raise
    except Exception as exc:
        if extra is not None:
            raise TrustStoreError(f"could not configure {CA_PATH_ENV}: {exc}") from exc
        return f"could not read the system trust store: {exc}"
    return None


__all__ = ["CA_PATH_ENV", "DISABLE_ENV", "TrustStoreError", "use_system_trust_store"]
