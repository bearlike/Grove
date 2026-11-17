"""The key two daemons share, and how each side proves it holds it.

Identity between daemons is a mutually generated key and nothing else. The hub
mints a one-time join token; a node presents it once; at that first contact the
hub generates a per-node secret, persists a digest of it in ``nodes.json`` and
returns it, and the node persists it in a 0600 ``node-key``. Every later call
carries :class:`NodeBearer`, an HMAC over a timestamp keyed by that secret.

**What the hub stores is a signing key, not a password hash, and that is forced.**
An HMAC cannot be verified against a one-way hash of its key, so the hub keeps
``sha256(secret)`` and both sides sign with that. Reading ``nodes.json`` is
therefore enough to impersonate a node, exactly as reading ``node-key`` is; the
file is 0600 and lives beside ``auth.json`` for that reason. The digest still
buys one thing: the secret a node was handed never sits on the hub's disk.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from grove.core import paths
from grove.core.errors import GroveError
from grove.core.record_file import RecordCodec, RecordFile

_FILE_VERSION = 1
_BEARER_PREFIX = "grove-node-v1"


class NodeAuthRefused(GroveError):
    """The other side does not hold, or no longer accepts, this node's key."""


@dataclass(frozen=True, slots=True)
class JoinToken:
    """A one-time token a hub minted for one future node. Only its hash is stored."""

    token_hash: str
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class NodeRecord:
    """A node the hub has admitted: its signing key and where to reach it."""

    node_id: str
    name: str
    signing_key: str
    advertise_url: str
    joined_at: datetime


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class NodeCatalogFile:
    """The hub's durable half: join tokens and admitted nodes, in ``nodes.json``.

    Only what cannot be re-learnt lives here. Which workspaces a node owns, and
    when it was last seen, are rebuilt from its next registration and kept in
    memory (:class:`grove.core.federation.hub.Hub`).
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._file: RecordFile[JoinToken | NodeRecord] = RecordFile(
            path or paths.federation_nodes_path(),
            store="federation-nodes",
            version=_FILE_VERSION,
            codec=RecordCodec(key=_key, encode=_encode, decode=_decode),
            on_unreadable="raise",
            mode=0o600,
        )

    def mint_join_token(self, ttl: timedelta) -> str:
        """A fresh single-use token; the plaintext is returned once and never stored."""
        token = f"grove-join-{secrets.token_urlsafe(32)}"
        now = self._clock()
        record = JoinToken(token_hash=_hash(token), created_at=now, expires_at=now + ttl)
        self._file.mutate(lambda records: {**self._live(records, now), _key(record): record})
        return token

    def admit(self, token: str, *, name: str, advertise_url: str) -> tuple[NodeRecord, str]:
        """Spend ``token`` and admit a node; return its record and its raw secret.

        The spend and the admission are one write, so a token can never admit two
        nodes, even from two concurrent requests.
        """
        now = self._clock()
        secret = secrets.token_urlsafe(32)
        node = NodeRecord(
            node_id=f"nd_{secrets.token_hex(16)}",
            name=name,
            signing_key=_hash(secret),
            advertise_url=advertise_url,
            joined_at=now,
        )
        key = f"t:{_hash(token)}"

        def change(records: Mapping[str, JoinToken | NodeRecord]) -> dict[str, Any]:
            found = records.get(key)
            if not isinstance(found, JoinToken) or found.used_at or found.expires_at <= now:
                raise NodeAuthRefused("join token is unknown, already used or expired")
            spent = replace(found, used_at=now)
            return {**self._live(records, now), key: spent, _key(node): node}

        self._file.mutate(change)
        return node, secret

    def node(self, node_id: str) -> NodeRecord | None:
        record = self._file.get(f"n:{node_id}")
        return record if isinstance(record, NodeRecord) else None

    def nodes(self) -> list[NodeRecord]:
        return sorted(
            (r for r in self._file.records().values() if isinstance(r, NodeRecord)),
            key=lambda r: r.joined_at,
        )

    def update_address(self, node_id: str, *, name: str, advertise_url: str) -> None:
        key = f"n:{node_id}"

        def change(records: Mapping[str, JoinToken | NodeRecord]) -> dict[str, Any] | None:
            found = records.get(key)
            if not isinstance(found, NodeRecord):
                return None
            if (found.name, found.advertise_url) == (name, advertise_url):
                return None
            return {**records, key: replace(found, name=name, advertise_url=advertise_url)}

        self._file.mutate(change)

    def forget(self, node_id: str) -> bool:
        """Revoke a node's key. Its next call is refused and its link stops."""
        key = f"n:{node_id}"
        removed: list[bool] = [False]

        def change(records: Mapping[str, JoinToken | NodeRecord]) -> dict[str, Any] | None:
            if key not in records:
                return None
            removed[0] = True
            return {k: v for k, v in records.items() if k != key}

        self._file.mutate(change)
        return removed[0]

    @staticmethod
    def _live(
        records: Mapping[str, JoinToken | NodeRecord], now: datetime
    ) -> dict[str, JoinToken | NodeRecord]:
        """Drop tokens that can never be spent again, on every write."""
        return {
            k: v
            for k, v in records.items()
            if not (isinstance(v, JoinToken) and (v.used_at or v.expires_at <= now))
        }


class NodeKey:
    """The node's durable half: its id and secret, 0600, beside ``auth.json``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.federation_node_key_path()

    def load(self) -> tuple[str, str] | None:
        """``(node_id, signing_key)``, or ``None`` when this node has not joined."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise GroveError(f"node key at {self.path} is unreadable: {exc}") from exc
        return str(data["node_id"]), str(data["signing_key"])

    def store(self, node_id: str, secret: str) -> None:
        paths.write_atomic(
            self.path,
            json.dumps({"node_id": node_id, "signing_key": _hash(secret)}),
            mode=0o600,
        )

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


Audience = Literal["hub", "node"]


class NodeBearer:
    """``grove-node-v1.<node_id>.<unix ts>.<hmac>``: one side's per-request proof.

    Both directions sign with the same per-node key, so the MAC also covers the
    AUDIENCE: a bearer a node sent the hub cannot be replayed at that node as if
    the hub had sent it. The timestamp bounds replay to ``max_skew_seconds``;
    there is no nonce, because a re-registration replaces, a heartbeat
    refreshes, and a forwarded verb carries the verb's own semantics.
    """

    @staticmethod
    def sign(
        node_id: str, signing_key: str, *, audience: Audience, now: float | None = None
    ) -> str:
        stamp = str(int(time.time() if now is None else now))
        mac = _mac(signing_key, node_id, stamp, audience)
        return f"{_BEARER_PREFIX}.{node_id}.{stamp}.{mac}"

    @staticmethod
    def node_id(bearer: str) -> str:
        """The node id a bearer claims, before it is verified."""
        parts = bearer.split(".")
        if len(parts) != 4 or parts[0] != _BEARER_PREFIX:
            raise NodeAuthRefused("malformed node bearer")
        return parts[1]

    @staticmethod
    def verify(
        bearer: str,
        signing_key: str,
        *,
        audience: Audience,
        max_skew_seconds: float,
        now: float | None = None,
    ) -> str:
        """Return the node id when the bearer is valid; raise otherwise."""
        node_id = NodeBearer.node_id(bearer)
        _, _, stamp, mac = bearer.split(".")
        try:
            issued = int(stamp)
        except ValueError as exc:
            raise NodeAuthRefused("malformed node bearer") from exc
        if abs((time.time() if now is None else now) - issued) > max_skew_seconds:
            raise NodeAuthRefused("node bearer is outside the allowed clock skew")
        if not hmac.compare_digest(mac, _mac(signing_key, node_id, stamp, audience)):
            raise NodeAuthRefused("node bearer signature does not match")
        return node_id


def _mac(signing_key: str, node_id: str, stamp: str, audience: Audience) -> str:
    return hmac.new(
        signing_key.encode("utf-8"), f"{audience}.{node_id}.{stamp}".encode(), hashlib.sha256
    ).hexdigest()


def _key(record: JoinToken | NodeRecord) -> str:
    if isinstance(record, JoinToken):
        return f"t:{record.token_hash}"
    return f"n:{record.node_id}"


def _encode(record: JoinToken | NodeRecord) -> dict[str, Any]:
    if isinstance(record, JoinToken):
        return {
            "kind": "join_token",
            "token_hash": record.token_hash,
            "created_at": record.created_at.isoformat(),
            "expires_at": record.expires_at.isoformat(),
            "used_at": record.used_at.isoformat() if record.used_at else None,
        }
    return {
        "kind": "node",
        "node_id": record.node_id,
        "name": record.name,
        "signing_key": record.signing_key,
        "advertise_url": record.advertise_url,
        "joined_at": record.joined_at.isoformat(),
    }


def _decode(data: dict[str, Any]) -> JoinToken | NodeRecord:
    if data["kind"] == "join_token":
        used = data.get("used_at")
        return JoinToken(
            token_hash=data["token_hash"],
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            used_at=datetime.fromisoformat(used) if used else None,
        )
    if data["kind"] == "node":
        return NodeRecord(
            node_id=data["node_id"],
            name=data["name"],
            signing_key=data["signing_key"],
            advertise_url=data["advertise_url"],
            joined_at=datetime.fromisoformat(data["joined_at"]),
        )
    raise ValueError(f"unknown federation record kind {data['kind']!r}")
