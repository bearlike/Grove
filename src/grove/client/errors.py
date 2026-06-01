"""Client-side error types.

The wire protocol returns ``{"detail": {"error": "...", "message": "..."}}``
for typed engine failures. ``GroveClient`` parses these and raises the
matching ``ProtocolError`` subclass. Transport-level failures (subprocess
died, SSH disconnected, port-forward broke) raise ``TransportError``.
"""

from __future__ import annotations


class GroveClientError(Exception):
    """Base for every error this SDK raises."""


class TransportError(GroveClientError):
    """The transport (subprocess or SSH connection) failed."""


class ProtocolError(GroveClientError):
    """The daemon returned a typed error envelope."""

    def __init__(self, *, code: str, message: str, status: int) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status


class NeedsPairingError(GroveClientError):
    """Remote backend has no daemon_token yet — caller must run the pairing flow.

    Carries the requesting backend's label and the resolved daemon URL so the
    client's pairing modal can show "Pair with <label>" and POST to the right
    daemon's pairing endpoints.
    """

    def __init__(self, backend_label: str, *, daemon_http_url: str | None = None) -> None:
        super().__init__(f"backend {backend_label!r} has not been paired yet")
        self.backend_label = backend_label
        self.daemon_http_url = daemon_http_url
