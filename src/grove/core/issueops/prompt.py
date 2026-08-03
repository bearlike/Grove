"""Rendering ``IssueOpsConfig.prompt_template`` — the ONE boot prompt.

Two paths start a workspace from a ticket: an ``@grove`` comment routed by
:class:`~grove.core.issueops.engine.IssueOpsEngine`, and an assignee pickup
driven by :class:`~grove.core.issueops.pickup.PickupEngine`. Both fill the same
template, because a second template mechanism means a deployment that tunes its
agent's marching orders in one place and silently does not in the other.

``{command_text}`` is what tells the two apart in the rendered text: the comment
path puts the human's own words there, the pickup path a sentence saying it was
assigned. That is why the placeholder did not need renaming when the second
caller arrived — it always answered "how were you engaged", and one of the two
answers is now "nobody typed anything".

The rendering is deliberately forgiving. A user-overridden template naming a
placeholder Grove does not supply renders that name literally rather than
raising, and a template with stray braces degrades to the raw command text —
a bad override must cost the user a badly-worded prompt, never a workspace that
refuses to start.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from grove.core.contracts.tickets import TicketComment

_NO_COMMENTS = "(no comments on this ticket yet)"


class _SafeFormatMap(dict[str, object]):
    """``str.format_map`` backing that renders an unknown ``{placeholder}`` literally.

    A user-overridden ``prompt_template`` that references a name the engine
    doesn't supply must not crash the router — the missing key comes back as its
    own ``{name}`` text instead of raising ``KeyError``.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class IssuePrompt:
    """Turns a ticket (and how Grove was engaged) into the agent's first message.

    A class rather than two free functions because the two halves are one rule:
    how a thread reads and how it is spliced into the template are the same
    decision, and splitting them is how a caller ends up rendering the thread
    twice or not at all.
    """

    @staticmethod
    def render_thread(comments: Sequence[TicketComment]) -> str:
        """The whole comment thread, in the tracker's own order, as plain text.

        In order because the argument developed in that order — a thread read out
        of sequence is actively misleading about what was decided last. Authors
        are named because "who said this" changes how an agent weighs it, and
        nothing is truncated: the agent is about to spend a long session on this
        ticket, and the discussion is the cheapest context it will ever get.
        """
        if not comments:
            return _NO_COMMENTS
        blocks: list[str] = []
        for comment in comments:
            who = comment.author or "someone"
            when = f" on {comment.created_at}" if comment.created_at else ""
            blocks.append(f"--- {who}{when} ---\n{comment.body.strip()}")
        return "\n\n".join(blocks)

    @staticmethod
    def render(
        template: str,
        *,
        number: str | int,
        title: str,
        body: str,
        url: str,
        command_text: str,
        comments: str,
    ) -> str:
        """Fill the template, degrading to ``command_text`` on a malformed one."""
        values = _SafeFormatMap(
            title=title,
            body=body,
            number=number,
            url=url,
            command_text=command_text,
            comments=comments,
        )
        try:
            return template.format_map(values)
        except (ValueError, IndexError) as exc:  # stray/positional braces in an override
            logger.warning("issue-ops prompt template is malformed, using raw command: {}", exc)
            return command_text


__all__ = ["IssuePrompt"]
