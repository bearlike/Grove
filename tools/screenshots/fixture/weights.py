"""Drawing one member of a ``(value, weight)`` table.

Deliberately one free function rather than a class: three separate models own a
weight table (models per provider, tools per call, context windows per session)
and the only thing they share is this draw. Wrapping it in a type would make
every one of those tables a nested object in `demo.json` for no gain, and the
alternative — a private copy beside each caller — is the duplication this whole
refactor exists to remove.
"""

from __future__ import annotations

import random
from collections.abc import Sequence


def weighted[T](rng: random.Random, table: Sequence[tuple[T, float]]) -> T:
    """One member of ``table``, chosen with probability proportional to weight."""
    return rng.choices([value for value, _ in table], weights=[weight for _, weight in table])[0]
