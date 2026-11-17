"""Slack: start and steer workspaces from a thread, the way Devin does.

Mention the app (or DM it, or use its message shortcut) and a workspace starts
with the conversation as its prompt; replies in the thread steer it; the agent's
replies come back into the thread. See ``CLAUDE.md`` here for the design.
"""

from grove.core.slack.bridge import SlackBridge
from grove.core.slack.events import MentionCommand, SlackEvent, SlackFile, SlackMessage
from grove.core.slack.gateway import SlackError, SlackGateway
from grove.core.slack.threads import SlackThread, SlackThreadLog

__all__ = [
    "MentionCommand",
    "SlackBridge",
    "SlackError",
    "SlackEvent",
    "SlackFile",
    "SlackGateway",
    "SlackMessage",
    "SlackThread",
    "SlackThreadLog",
]
