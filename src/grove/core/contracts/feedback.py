"""A human's rating of one agent turn — the body of ``POST …/sessions/{sid}/feedback``."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from grove.core.telemetry.feedback import Rating

__all__ = ["TurnFeedbackRequest"]


class TurnFeedbackRequest(BaseModel):
    """Rate the turn that began at ``started_at``.

    The turn is addressed by the ``started_at`` the transcript already carries
    rather than by a trace id: the daemon re-derives the trace, so a client
    cannot annotate a trace the replay would never have produced. Re-sending
    for the same turn replaces the previous rating.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    started_at: datetime
    rating: Rating
    reasons: list[str] = Field(default_factory=list, max_length=16)
    """Reasons from ``WhoamiView.feedback_reasons``; anything else is refused."""
    # `default_factory`, not `default=""`: a `default` key makes the generated
    # TypeScript property REQUIRED.
    note: str = Field(default_factory=str, max_length=2000)
    """Free text a person adds to a rating; recorded as the score's comment."""
