"""Named terminal keys, not arbitrary tmux input or a widget-driving grammar."""

from pydantic import BaseModel, ConfigDict

from grove.core.tmux import SendKey

__all__ = ["SendKey", "SendKeysRequest"]


class SendKeysRequest(BaseModel):
    """Deliver exactly one named key to the workspace's own live terminal.

    No caller-selected pane, command prefix, repetition count or raw bytes.
    Acknowledgement means delivered, not that the application accepted an action.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: SendKey
