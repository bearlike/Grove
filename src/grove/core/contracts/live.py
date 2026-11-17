"""What a native session is doing THIS second, for a loader someone is watching.

Separate from the ~1 Hz activity view on purpose: these facts move several times
a second while a model thinks or streams, so they ride a per-workspace stream
that exists only while a viewer holds it open, never the host-wide delta bus.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

LiveStep = Literal["thinking", "generating", "tool", "subagent"]


class LivePulseView(BaseModel):
    """One snapshot of the owned stream's current step.

    ``step`` is ``None`` while the owner has seen nothing it can name since the
    last turn ended. Counts are the harness's own figures, never an estimate
    Grove makes: ``thinking_tokens`` is Claude's running ``estimated_tokens``,
    ``output_tokens`` is the sum of ``usage.output_tokens`` the turn's replies
    reported. ``None`` means the stream has not stated it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    step: LiveStep | None = None
    since: datetime | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None
    tool: str | None = None
    tool_detail: str | None = None
    subagents: int = 0
    subagent: str | None = None
    subagent_tool: str | None = None


__all__ = ["LivePulseView", "LiveStep"]
