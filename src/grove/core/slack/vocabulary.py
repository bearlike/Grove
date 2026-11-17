"""Every Slack word Grove sends or matches, named once.

Event types, interactive payload types, the shortcut's callback id, the
keywords a person types after the mention, the reactions Grove adds, and the
size limits Slack enforces. Nothing else in the package spells one of these.
"""

from __future__ import annotations

from typing import ClassVar, Final


class SlackEvents:
    """Socket Mode envelope and event names Grove listens for."""

    EVENTS_API: ClassVar[str] = "events_api"
    INTERACTIVE: ClassVar[str] = "interactive"
    MENTION: ClassVar[str] = "app_mention"
    MESSAGE: ClassVar[str] = "message"
    MESSAGE_ACTION: ClassVar[str] = "message_action"
    # The message shortcut's callback id. It must match the app manifest in
    # docs/features-slack.md, which is where a workspace admin copies it from.
    START_SHORTCUT: ClassVar[str] = "grove_start_session"
    # Direct messages need no mention: every DM to the app is addressed to it.
    DIRECT_CHANNEL: ClassVar[str] = "im"
    # Subtypes that are still a person writing. Every other subtype (edits,
    # deletions, joins, bot posts) is ignored, because acting on an edit would
    # fire the same request twice.
    HUMAN_SUBTYPES: ClassVar[frozenset[str]] = frozenset({"", "file_share", "thread_broadcast"})


class SlackKeywords:
    """Words a person types after the mention, and the Slack-only thread verbs."""

    NEW: ClassVar[str] = "!new"
    PROJECT: ClassVar[str] = "!project"
    AGENT: ClassVar[str] = "!agent"
    # Either form anywhere in a thread reply keeps Grove from forwarding it.
    ASIDE: ClassVar[tuple[str, ...]] = ("!aside", "(aside)")
    MUTE: ClassVar[str] = "mute"
    UNMUTE: ClassVar[str] = "unmute"
    # Options that take the next word as their value.
    VALUED: ClassVar[frozenset[str]] = frozenset({PROJECT, AGENT})


class SlackReactions:
    """Emoji names Grove adds to the message that engaged it.

    The "working" spinner is not here: it is `slack.working_reaction` in config,
    so a workspace with a custom animated emoji can use it.
    """

    RECEIVED: ClassVar[str] = "eyes"
    FAILED: ClassVar[str] = "x"
    # Acknowledges a verb that needs no reply (mute, pause, stop, ...).
    DONE: ClassVar[str] = "ok_hand"


#: Slack caps the markdown blocks of one message at 12,000 characters in total.
#: A longer reply is split into several posts below that.
MARKDOWN_CHUNK: Final = 11_000

#: `CreateWorkspaceRequest.initial_prompt` refuses anything longer.
PROMPT_LIMIT: Final = 10_000

#: `CreateWorkspaceRequest.attachments` refuses more than this many files.
ATTACHMENT_LIMIT: Final = 20

#: Workspace lifecycle events a thread hears about, and what it is told. Only the
#: two that end the work: a thread stays quiet about everything else.
LIFECYCLE_LINES: Final[dict[str, str]] = {
    "killed": "Session ended. Mention me again to start a new one.",
    "error": "The session hit an error",
}

__all__ = [
    "ATTACHMENT_LIMIT",
    "LIFECYCLE_LINES",
    "MARKDOWN_CHUNK",
    "PROMPT_LIMIT",
    "SlackEvents",
    "SlackKeywords",
    "SlackReactions",
]
