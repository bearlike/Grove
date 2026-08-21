"""Per-project protection for public workspace shares.

One project policy carries both controls because readers enter through a project
share link: a passcode belongs to the repo, not to each workspace, and a TTL is
applied once when a link is issued. Existing links deliberately retain the expiry
they were stamped with; changing a project's TTL affects only new and re-issued
links, avoiding an unauthenticated cross-store lookup during share resolution.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from grove.core import paths
from grove.core.errors import GroveError

_VERSION = 1


class _StoreData(TypedDict):
    policies: dict[str, SharePolicy]


@dataclass(frozen=True, slots=True)
class SharePolicy:
    """A public-share policy for one resolved repository root.

    ``passcode_hash`` is SHA-256 hex, never the plaintext. Keeping minting and
    verification on this type prevents a caller from accidentally hashing with
    one rule and comparing with another.
    """

    repo_root: Path
    passcode_hash: str | None = None
    ttl_seconds: int | None = None

    @classmethod
    def for_repo(
        cls,
        repo_root: Path,
        *,
        passcode: str | None = None,
        ttl_seconds: int | None = None,
    ) -> SharePolicy:
        """Build a policy from user-supplied plaintext, never retaining it."""
        return cls(
            repo_root=repo_root.resolve(),
            passcode_hash=cls.hash_passcode(passcode) if passcode is not None else None,
            ttl_seconds=ttl_seconds,
        )

    @staticmethod
    def hash_passcode(passcode: str) -> str:
        """Return the stored SHA-256 representation of one passcode."""
        return hashlib.sha256(passcode.encode("utf-8")).hexdigest()

    def verifies(self, candidate: str | None) -> bool:
        """Whether a submitted plaintext satisfies this policy's passcode."""
        if self.passcode_hash is None:
            return True
        if candidate is None:
            return False
        return secrets.compare_digest(self.passcode_hash, self.hash_passcode(candidate))


class SharePolicyStore:
    """Atomic JSON persistence for per-project public-share policy.

    Like ``JsonWorkspaceStore``, every mutation holds a cross-process lock over
    the whole read-modify-write and publishes via an atomic rename. Legacy or
    malformed individual policy rows degrade to an unprotected policy: the file
    contains optional safeguards, never workspace identity or capabilities.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else paths.share_policies_path()

    @property
    def path(self) -> Path:
        return self._path

    def get(self, repo_root: Path) -> SharePolicy:
        """Return the policy for a resolved repo, or its no-protection default."""
        resolved = repo_root.resolve()
        return self._load()["policies"].get(str(resolved), SharePolicy(repo_root=resolved))

    def save(self, policy: SharePolicy) -> SharePolicy:
        """Replace one policy by canonical repo root and persist it atomically."""
        resolved = policy.repo_root.resolve()
        canonical = SharePolicy(
            repo_root=resolved,
            passcode_hash=policy.passcode_hash,
            ttl_seconds=policy.ttl_seconds,
        )
        with paths.exclusive_lock(self._path):
            data = self._load()
            data["policies"][str(resolved)] = canonical
            self._write(data)
        return canonical

    def _load(self) -> _StoreData:
        if not self._path.exists():
            return {"policies": {}}
        try:
            with self._path.open(encoding="utf-8") as fh:
                raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise GroveError(f"corrupt share policy file at {self._path}: {exc}") from exc
        except OSError as exc:
            raise GroveError(f"cannot read share policy file at {self._path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise GroveError(f"unexpected share policy shape at {self._path}")
        if raw.get("version") != _VERSION:
            raise GroveError(
                f"share policy version {raw.get('version')!r} not supported (expected {_VERSION})"
            )
        rows = raw.get("policies", {})
        if not isinstance(rows, dict):
            raise GroveError(f"`policies` must be an object in {self._path}")
        policies: dict[str, SharePolicy] = {}
        for repo_root, row in rows.items():
            policy = self._decode_policy(repo_root, row)
            if policy is not None:
                policies[str(policy.repo_root)] = policy
        return {"policies": policies}

    def _write(self, data: _StoreData) -> None:
        payload: dict[str, Any] = {
            "version": _VERSION,
            "policies": {
                root: {
                    "passcode_hash": policy.passcode_hash,
                    "ttl_seconds": policy.ttl_seconds,
                }
                for root, policy in data["policies"].items()
            },
        }
        paths.write_atomic(self._path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _decode_policy(repo_root: object, row: object) -> SharePolicy | None:
        """Decode one row defensively; an old row without fields is unprotected."""
        if not isinstance(repo_root, str) or not isinstance(row, dict):
            return None
        try:
            canonical = Path(repo_root).resolve()
        except OSError:
            return None
        passcode_hash = row.get("passcode_hash")
        ttl_seconds = row.get("ttl_seconds")
        if not isinstance(passcode_hash, str):
            passcode_hash = None
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            ttl_seconds = None
        return SharePolicy(
            repo_root=canonical,
            passcode_hash=passcode_hash,
            ttl_seconds=ttl_seconds,
        )


__all__ = ["SharePolicy", "SharePolicyStore"]
