"""Capture: routes, testids, viewport, framing, sandbox lifecycle, daemon fixtures.

Knows nothing about what the demo data SAYS — it drives whatever `planter/` left
on disk and frames whatever came back.

`Sandbox` is the only name re-exported here, and that is a constraint rather
than a preference: every other module in this package imports grove, while a
capture must redirect its environment BEFORE the first such import. Import the
rest explicitly, after `Sandbox(...).activate()` has run.
"""

from .sandbox import Sandbox

__all__ = ["Sandbox"]
