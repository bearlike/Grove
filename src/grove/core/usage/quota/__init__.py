"""grove.core.usage.quota — what remains, per billing account.

The sibling seam to ``grove.core.agents``: an ``AgentAdapter`` reads what a tool
already did, a :class:`QuotaProvider` reads what a tool's account has left. One
provider per coding tool, one :class:`QuotaCollector` over all of them, and an
account minted per *profile root* rather than per tool — so two Claude config
dirs and two Codex homes are four independently refreshable accounts.

Public surface is the collector plus the two concrete providers and the account
identity; everything else (credential reading, endpoint shapes, rollout tail
scanning) is provider-internal by design, because those are exactly the parts
that change when a vendor moves something.
"""

from __future__ import annotations

from grove.core.usage.quota.base import QuotaAccount, QuotaProvider
from grove.core.usage.quota.claude import ClaudeQuotaProvider
from grove.core.usage.quota.codex import CodexQuotaProvider
from grove.core.usage.quota.collector import QuotaCollector

__all__ = [
    "ClaudeQuotaProvider",
    "CodexQuotaProvider",
    "QuotaAccount",
    "QuotaCollector",
    "QuotaProvider",
]
