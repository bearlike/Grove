"""Wire shapes for a project's public-share policy."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SharePolicyView(BaseModel):
    """The policy an authenticated client may inspect, without its secret."""

    model_config = ConfigDict(frozen=True)

    ttl_seconds: int | None = None
    passcode_set: bool = False


class SharePolicyUpdateRequest(BaseModel):
    """Replace a project's public-share policy.

    Both values are required so ``null`` has one unambiguous meaning: clear the
    corresponding protection. The plaintext passcode is accepted only to produce
    a hash and is never returned through ``SharePolicyView``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ttl_seconds: int | None = Field(default=..., gt=0)
    passcode: str | None = Field(default=..., min_length=1, max_length=1024)


__all__ = ["SharePolicyUpdateRequest", "SharePolicyView"]
