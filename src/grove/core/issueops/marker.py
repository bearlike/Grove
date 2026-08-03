"""The shared HTML-comment markers between the issue-ops engine and its publisher.

Two invisible signatures, two jobs — split across two constants so the general
anti-loop guard and the sticky-comment recovery scan can't collide:

- :data:`SIGNATURE_MARKER` rides EVERY Grove issue-ops comment — the engine's
  usage/refusal replies AND the publisher's sticky status comment.
  The engine drops any inbound comment whose body carries it, closing the
  feedback loop a comment-driven bot would otherwise create: the bot's own
  comment must never re-trigger the bot.
- :data:`STICKY_MARKER` rides ONLY the publisher's sticky status comment. The
  publisher re-finds *that one comment* by it on a cold start; scanning for the
  general signature instead would adopt an old reply and edit the status render
  over it (the recovery/reply collision this split exists to prevent).

Both are HTML comments — invisible in rendered markdown, trivially
substring-matched. They live in their own tiny module (not on the engine or the
publisher) precisely because BOTH faces import them and neither should depend on
the other: the engine must not import publisher/render logic, and the publisher
must not import the routing engine. Two constants, one owner, no cycle.
"""

from __future__ import annotations

SIGNATURE_MARKER = "<!-- grove:issue-ops -->"
"""Invisible signature on EVERY Grove issue-ops comment (reply + sticky status).

Substring-matched (not parsed): the engine's ``SIGNATURE_MARKER in body`` guard
is the belt to the bot-actor check's suspenders, so a Grove comment re-surfaced
as an event (a quote, an edit) is dropped even if the actor heuristic misses.
Because the sticky comment stamps it too, the one guard catches BOTH bot
artifacts.
"""

STICKY_MARKER = "<!-- grove:issue-ops:status -->"
"""Invisible signature on ONLY the publisher's sticky status comment.

More specific than :data:`SIGNATURE_MARKER` so cold-start recovery adopts the
*sticky* comment and never an old usage/refusal reply (which carries the general
signature but not this more specific one). The sticky footer stamps both — this
for the recovery scan, the signature for the engine's anti-loop guard.
"""

__all__ = ["SIGNATURE_MARKER", "STICKY_MARKER"]
