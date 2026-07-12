"""Issue-ops wire shapes — what the CI forwarder POSTs, and what it gets back (#196).

``IssueOpsEvent`` IS the request body the ``.gitea/`` (or GitHub Actions)
composite forwarder sends to ``POST /issue-ops/events``: a normalized, provider-
neutral snapshot of one issue-comment event. The forwarder is a stateless dumb
pipe (the research verdict) — it asserts the actor's permission and bot-ness from
the forge payload it holds, and the engine trusts those assertions rather than
re-fetching. Pydantic, here in ``contracts/``, because a non-Python CI action
constructs it.

``IssueOpsOutcome`` is the 202 response the same action reflects into a reaction
on the comment (👀→🚀/👎): ``action`` names what happened, ``code`` qualifies a
``refused``/``ignored``, ``workspace_id`` links the affected workspace when there
is one. The action posts reactions itself (it holds the forge event token); the
engine only names the result.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grove.core.contracts.tickets import TicketProviderName

_FROZEN = ConfigDict(extra="forbid", frozen=True)

IssueOpsAction = Literal[
    "created",  # a new workspace was created for the ticket
    "steered",  # an existing running workspace was sent the comment as a follow-up
    "paused",  # the ticket's workspace was paused
    "resumed",  # the ticket's workspace was resumed
    "stopped",  # the ticket's workspace was killed
    "status",  # a status re-render was triggered (the publisher seam, #197)
    "refused",  # a well-formed command the engine declined — ``code`` says why
    "ignored",  # dropped before routing (duplicate / bot / marker / …) — ``code`` says why
]
"""What the engine did with one event. ``refused``/``ignored`` carry a ``code``;
the rest are terminal verbs the CI action maps to an outcome reaction. Defined
here (not on the engine) so both the engine and this view share one source and
contracts never imports the engine."""


class IssueOpsEvent(BaseModel):
    """One normalized issue-comment event the CI forwarder POSTs to the daemon.

    Provider-neutral and self-contained: the engine never calls back to the forge
    to enrich it. ``actor_permission`` / ``actor_is_bot`` are the forwarder's
    assertions (it read them off the webhook payload with the event token); the
    engine's permission and bot-drop policies act on those verbatim — patching the
    provider boundary, not re-deriving forge state.
    """

    model_config = _FROZEN

    provider: TicketProviderName
    """Which tracker this issue lives on — selects the repo's provider config and
    the reply-comment credential."""

    owner: str = Field(min_length=1)
    """Repo owner/org, matched against the provider config's ``owner`` to resolve
    the target repo (a bare ticket id is ambiguous across repos)."""

    repo: str = Field(min_length=1)
    """Repo name, matched with ``owner`` against the provider config."""

    issue_number: int = Field(ge=1)
    """The issue's number — the canonical ticket key for the numeric trackers
    (Gitea/GitHub). Stringified when it crosses to ``find_by_ticket`` /
    ``TicketSelector``."""

    issue_title: str = ""
    """The issue title, seeds the workspace title and the boot prompt."""

    issue_body: str = ""
    """The issue body, fills the boot prompt's ``{body}``."""

    issue_url: str = ""
    """The issue's web URL, fills the boot prompt's ``{url}``. Carried on the event
    (the forwarder has it from the payload) so the engine needn't reconstruct a
    per-provider URL shape."""

    comment_id: str = Field(min_length=1)
    """The triggering comment's provider id — the dedupe key (CI retries deliver
    the same event at-least-once) and the reaction target the CI action holds."""

    comment_body: str = ""
    """The comment text the engine parses for the trigger token and command."""

    actor: str = Field(min_length=1)
    """The commenter's login — checked against the ``allowed_actors`` widening
    knob, and the identity the permission policy governs."""

    actor_permission: str = ""
    """The commenter's repo permission as ASSERTED by the forwarder (``admin`` /
    ``write`` / ``read`` / ``none`` / …). The default policy honors a command only
    at write-or-above; the engine never re-fetches it."""

    actor_is_bot: bool = False
    """Whether the forwarder flagged the actor as a bot account. Bot comments are
    dropped before routing — the universal "ignore machine actors" default that
    keeps a comment-driven bot from answering itself."""


class IssueOpsOutcome(BaseModel):
    """The 202 body describing what the engine did — the CI action turns it into a reaction.

    ``action`` is the terminal verb; ``code`` qualifies a ``refused`` (``usage`` /
    ``insufficient_permission`` / ``no_workspace`` / …) or an ``ignored``
    (``duplicate`` / ``bot`` / ``signature_marker`` / ``not_triggered`` /
    ``unknown_repo``). ``workspace_id`` is set whenever the action touched a
    concrete workspace, so the action can deep-link it.
    """

    model_config = _FROZEN

    action: IssueOpsAction
    code: str | None = None
    workspace_id: str | None = None


__all__ = ["IssueOpsAction", "IssueOpsEvent", "IssueOpsOutcome"]
