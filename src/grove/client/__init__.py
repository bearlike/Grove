"""Grove client SDK — single transport for both local and remote backends.

Public surface:
    GroveClient (Task 12)
    BackendConfig
    GroveClientError, TransportError, ProtocolError
"""

from __future__ import annotations

from grove.client.backend import BackendConfig
from grove.client.client import GroveClient
from grove.client.errors import GroveClientError, ProtocolError, TransportError

__all__ = [
    "BackendConfig",
    "GroveClient",
    "GroveClientError",
    "ProtocolError",
    "TransportError",
]
