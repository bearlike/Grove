"""What a project IS across machines: its remote and its default branch, not a path.

A path names a project on one node only. A hub naming a project to a node that
has never seen its path needs a name both can derive independently, and the
repository's own remote is the one thing every clone of it shares. The path
stays a per-node fact that each node resolves for itself.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

# `git@host:owner/repo.git`, the scp-like form git accepts without a scheme.
_SCP = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>[^/].*)$")


class ProjectIdentity(BaseModel):
    """``remote`` is normalized (``host/owner/repo``) so every clone URL form agrees."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    remote: str = Field(min_length=1, max_length=512)
    default_branch: str = Field(min_length=1, max_length=255)

    @property
    def key(self) -> str:
        return f"{self.remote}@{self.default_branch}"

    @staticmethod
    def normalize_remote(url: str) -> str | None:
        """``host/owner/repo``, lowercase host, no user, port, scheme or ``.git``.

        ``None`` for something that names no host (a local path remote), because
        a path is exactly the per-node fact this identity exists to replace.
        """
        url = url.strip()
        if "://" in url:
            parts = urlsplit(url)
            host, path = parts.hostname or "", parts.path
        elif match := _SCP.match(url):
            host, path = match["host"], match["path"]
        else:
            return None
        path = path.strip("/").removesuffix(".git").strip("/")
        if not host or not path:
            return None
        return f"{host.lower()}/{path}"


__all__ = ["ProjectIdentity"]
