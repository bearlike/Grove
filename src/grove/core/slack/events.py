"""Slack payloads as Grove reads them: files, messages, events and mention commands.

Slack's JSON is narrowed here, once, into frozen dataclasses. Everything past
this module works with these types and never indexes a raw payload. Pure: no
network, no clock.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from grove.core.issueops.parser import CommandParser, ParsedCommand
from grove.core.slack.vocabulary import SlackEvents, SlackKeywords


@dataclass(frozen=True, slots=True)
class SlackFile:
    """One file shared in a Slack message.

    Attributes:
        name: The file's own name, as the person uploaded it.
        url: The private download URL, readable with the bot token.
        size: Size in bytes, or 0 when Slack did not say.
        permalink: The file's page in Slack, for a prompt that cannot carry it.
    """

    name: str
    url: str
    size: int = 0
    permalink: str = ""

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> SlackFile | None:
        """Read one entry of a message's ``files`` list.

        Returns:
            The file, or ``None`` for an entry with no download URL (a deleted
            file or an external link).
        """
        url = str(raw.get("url_private_download") or raw.get("url_private") or "")
        if not url:
            return None
        return cls(
            name=str(raw.get("name") or raw.get("title") or "file"),
            url=url,
            size=int(raw.get("size") or 0),
            permalink=str(raw.get("permalink") or ""),
        )


@dataclass(frozen=True, slots=True)
class SlackMessage:
    """One message read back from a channel or a thread.

    Attributes:
        ts: Slack's message timestamp, which is also its id in the channel.
        user: The author's member id, or empty for an app's post.
        text: The raw Slack text, mentions and links still encoded.
        files: Files attached to the message.
        from_bot: Whether an app wrote it.
    """

    ts: str
    user: str
    text: str
    files: tuple[SlackFile, ...] = ()
    from_bot: bool = False

    @classmethod
    def from_payload(cls, raw: Mapping[str, Any]) -> SlackMessage:
        """Read one message from ``conversations.replies`` or ``.history``."""
        files = tuple(
            f for f in (SlackFile.from_payload(item) for item in raw.get("files") or []) if f
        )
        return cls(
            ts=str(raw.get("ts", "")),
            user=str(raw.get("user") or ""),
            text=str(raw.get("text") or ""),
            files=files,
            from_bot=bool(raw.get("bot_id")) or raw.get("subtype") == "bot_message",
        )


@dataclass(frozen=True, slots=True)
class SlackEvent:
    """A person's message that may concern Grove, from any of the three sources.

    Attributes:
        channel: The channel (or DM) id.
        ts: The message's own timestamp.
        thread_ts: The thread's root timestamp, or ``None`` for a top level message.
        user: Who sent it, or for the shortcut, who clicked it.
        text: The message text.
        files: Files attached to the message.
        addressed: Whether the message is for Grove: it mentions the app, is a
            DM to it, or came from the shortcut.
        shortcut: Whether it came from the "start a Grove session" shortcut.
    """

    channel: str
    ts: str
    thread_ts: str | None
    user: str
    text: str
    files: tuple[SlackFile, ...] = ()
    addressed: bool = False
    shortcut: bool = False

    @property
    def root_ts(self) -> str:
        """The thread this message belongs to, which is its own ts when top level."""
        return self.thread_ts or self.ts

    @property
    def in_thread(self) -> bool:
        """Whether the message is a reply inside an existing thread."""
        return self.thread_ts is not None and self.thread_ts != self.ts

    @property
    def is_aside(self) -> bool:
        """Whether the author marked it as not for Grove."""
        lowered = self.text.lower()
        return any(marker in lowered for marker in SlackKeywords.ASIDE)

    @classmethod
    def from_event(cls, raw: Mapping[str, Any], *, bot_user_id: str) -> SlackEvent | None:
        """Read an ``app_mention`` or ``message`` event.

        Returns:
            The event, or ``None`` for anything that is not a person writing:
            an app's post (Grove's own included), an edit, a deletion or a join.
        """
        if raw.get("type") not in {SlackEvents.MENTION, SlackEvents.MESSAGE}:
            return None
        if raw.get("bot_id") or str(raw.get("subtype") or "") not in SlackEvents.HUMAN_SUBTYPES:
            return None
        user = str(raw.get("user") or "")
        if not user or user == bot_user_id:
            return None
        text = str(raw.get("text") or "")
        message = SlackMessage.from_payload(raw)
        return cls(
            channel=str(raw.get("channel", "")),
            ts=message.ts,
            thread_ts=str(raw["thread_ts"]) if raw.get("thread_ts") else None,
            user=user,
            text=text,
            files=message.files,
            addressed=(
                raw.get("type") == SlackEvents.MENTION
                or f"<@{bot_user_id}>" in text
                or raw.get("channel_type") == SlackEvents.DIRECT_CHANNEL
            ),
        )

    @classmethod
    def from_shortcut(cls, payload: Mapping[str, Any]) -> SlackEvent | None:
        """Read a ``message_action`` payload from the start-session shortcut.

        Returns:
            The event, with the shortcut message's text as the request and the
            clicker as the sender, or ``None`` for another shortcut.
        """
        if payload.get("type") != SlackEvents.MESSAGE_ACTION:
            return None
        if payload.get("callback_id") != SlackEvents.START_SHORTCUT:
            return None
        message = payload.get("message") or {}
        parsed = SlackMessage.from_payload(message)
        return cls(
            channel=str((payload.get("channel") or {}).get("id", "")),
            ts=parsed.ts,
            thread_ts=str(message["thread_ts"]) if message.get("thread_ts") else None,
            user=str((payload.get("user") or {}).get("id", "")),
            text=parsed.text,
            files=parsed.files,
            addressed=True,
            shortcut=True,
        )


@dataclass(frozen=True, slots=True)
class MentionCommand:
    """What a message addressed to Grove asks for.

    Attributes:
        command: The issue-ops grammar's reading of the request: a verb, a
            prompt, or a usage error. ``None`` for a Slack-only thread verb.
        thread_verb: ``mute`` or ``unmute``, or empty.
        new: Whether ``!new`` asked for a fresh workspace in this thread.
        project: The ``!project`` value, or empty.
        agent: The ``!agent`` value, or empty.
    """

    command: ParsedCommand | None
    thread_verb: str = ""
    new: bool = False
    project: str = ""
    agent: str = ""

    # The parser needs a trigger to anchor on. Slack mentions are stripped before
    # parsing, so this placeholder is the only trigger it ever sees.
    _TRIGGER = "@grove"

    @classmethod
    def parse(cls, text: str, *, bot_user_id: str) -> MentionCommand:
        """Read the request out of an addressed message.

        The bot's own mention is removed wherever it sits, leading ``!`` options
        are taken off the front, and the rest goes through the issue-ops grammar,
        so ``status`` / ``pause`` / ``resume`` / ``stop`` mean the same here as
        on a tracker.
        """
        body = text.replace(f"<@{bot_user_id}>", " ").strip()
        tokens = body.split()
        options: dict[str, str] = {}
        while tokens and tokens[0].lower().startswith("!"):
            word = tokens.pop(0).lower()
            if word in SlackKeywords.VALUED and tokens:
                options[word] = tokens.pop(0)
            elif word == SlackKeywords.NEW:
                options[word] = "1"
            else:
                tokens.insert(0, word)
                break
        rest = " ".join(tokens)
        new = SlackKeywords.NEW in options
        project = options.get(SlackKeywords.PROJECT, "")
        agent = options.get(SlackKeywords.AGENT, "")
        if rest.lower() in {SlackKeywords.MUTE, SlackKeywords.UNMUTE}:
            return cls(
                command=None, thread_verb=rest.lower(), new=new, project=project, agent=agent
            )
        command = CommandParser().parse(f"{cls._TRIGGER} {rest}", trigger=cls._TRIGGER)
        return cls(command=command, new=new, project=project, agent=agent)


class SlackText:
    """Slack's encoded message text, turned into what a person sees."""

    _USER = re.compile(r"<@([A-Z0-9]+)(?:\|([^>]*))?>")
    _CHANNEL = re.compile(r"<#([A-Z0-9]+)(?:\|([^>]*))?>")
    _LINK = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]*))?>")
    _SPECIAL = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
    # `&amp;` last, so an encoded `&lt;` in the original survives as text.
    _ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))

    @classmethod
    def plain(cls, text: str, *, name_of: Callable[[str], str]) -> str:
        """Decode mentions, channel links, URLs and HTML entities.

        Args:
            text: Slack's raw message text.
            name_of: Looks up a member id's display name.

        Returns:
            The text with ``<@U1>`` as ``@name``, ``<#C1|x>`` as ``#x`` and
            ``<url|label>`` as ``label (url)``.
        """
        text = cls._USER.sub(lambda m: "@" + (m.group(2) or name_of(m.group(1))), text)
        text = cls._CHANNEL.sub(lambda m: "#" + (m.group(2) or m.group(1)), text)
        text = cls._LINK.sub(
            lambda m: f"{m.group(2)} ({m.group(1)})" if m.group(2) else m.group(1), text
        )
        text = cls._SPECIAL.sub(lambda m: "@" + m.group(1), text)
        for encoded, decoded in cls._ENTITIES:
            text = text.replace(encoded, decoded)
        return text


__all__ = ["MentionCommand", "SlackEvent", "SlackFile", "SlackMessage", "SlackText"]
