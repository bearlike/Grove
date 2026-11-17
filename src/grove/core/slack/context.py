"""What a Slack conversation tells a new workspace: its prompt and its files."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass

from loguru import logger

from grove.core.contracts.attachments import AttachmentUploadRequest
from grove.core.slack.events import SlackEvent, SlackFile, SlackMessage, SlackText
from grove.core.slack.gateway import SlackError, SlackGateway
from grove.core.slack.vocabulary import ATTACHMENT_LIMIT, PROMPT_LIMIT


@dataclass(frozen=True, slots=True)
class SlackContext:
    """The conversation around one request, already read from Slack.

    Attributes:
        channel: The channel's name, for the prompt.
        author: Who asked, by display name.
        permalink: A link back to the request.
        transcript: One rendered block per message, oldest first.
        attachments: Files copied into the workspace.
        linked_files: Files too large to copy, as ``name (link)`` lines.
    """

    channel: str
    author: str
    permalink: str
    transcript: tuple[str, ...]
    attachments: tuple[AttachmentUploadRequest, ...] = ()
    linked_files: tuple[str, ...] = ()


class SlackContextReader:
    """Reads the conversation a request was made in, the way Devin does.

    A mention inside a thread reads the whole thread. A top level mention reads
    the channel's recent messages before it. Files in either are copied into
    the workspace when small enough, and linked otherwise. Every read is
    best-effort: Slack refusing one costs the agent context, never the workspace.
    """

    def __init__(self, gateway: SlackGateway, *, history: int, max_attachment_bytes: int) -> None:
        """Bind the reader to Slack.

        Args:
            gateway: The Slack connection.
            history: How many channel messages a top level mention reads.
            max_attachment_bytes: The largest file copied; larger ones are linked.
        """
        self._gateway = gateway
        self._history = history
        self._max_bytes = max_attachment_bytes

    def read(self, event: SlackEvent) -> SlackContext:
        """Gather the conversation around ``event``."""
        messages = self._messages(event)
        attachments, linked = self._files(messages)
        return SlackContext(
            channel=self._gateway.channel_name(event.channel),
            author=self._gateway.user_name(event.user),
            permalink=self._gateway.permalink(event.channel, event.ts),
            transcript=tuple(self._render(m) for m in messages),
            attachments=attachments,
            linked_files=linked,
        )

    def files(self, files: tuple[SlackFile, ...]) -> tuple[list[tuple[str, bytes]], list[str]]:
        """Download a thread reply's files for steering.

        Returns:
            The ``(name, bytes)`` pairs that fit, and ``name (link)`` lines for
            the rest.
        """
        fetched: list[tuple[str, bytes]] = []
        linked: list[str] = []
        for file in files[:ATTACHMENT_LIMIT]:
            data = self._download(file)
            if data is None:
                linked.append(f"{file.name} ({file.permalink or 'not downloadable'})")
            else:
                fetched.append((file.name, data))
        return fetched, linked

    def _messages(self, event: SlackEvent) -> list[SlackMessage]:
        """The thread, or recent history plus the request itself."""
        here = SlackMessage(ts=event.ts, user=event.user, text=event.text, files=event.files)
        try:
            if event.thread_ts is not None:
                return self._gateway.thread(event.channel, event.thread_ts) or [here]
            return [
                *self._gateway.history(event.channel, before_ts=event.ts, limit=self._history),
                here,
            ]
        except SlackError as exc:
            logger.warning(
                "slack: could not read the conversation, using the message alone: {}", exc
            )
            return [here]

    def _files(
        self, messages: list[SlackMessage]
    ) -> tuple[tuple[AttachmentUploadRequest, ...], tuple[str, ...]]:
        """Every file in the conversation, copied or linked."""
        shared = tuple(f for m in messages for f in m.files)
        fetched, linked = self.files(shared)
        uploads = tuple(
            AttachmentUploadRequest(name=name, content_base64=base64.b64encode(data).decode())
            for name, data in fetched
        )
        return uploads, tuple(linked)

    def _download(self, file: SlackFile) -> bytes | None:
        """One file's bytes, or ``None`` when it is too large or will not download."""
        try:
            return self._gateway.download(file, max_bytes=self._max_bytes)
        except SlackError as exc:
            logger.warning("slack: {}", exc)
            return None

    def _render(self, message: SlackMessage) -> str:
        """One message as ``--- name ---`` and its plain text."""
        who = "Grove" if message.from_bot else self._gateway.user_name(message.user)
        text = SlackText.plain(message.text, name_of=self._gateway.user_name).strip()
        names = ", ".join(f.name for f in message.files)
        if names:
            text = f"{text}\n[shared: {names}]".strip()
        return f"--- {who} ---\n{text}"


class SlackPrompt:
    """Fills `slack.prompt_template` and keeps it under the create limit."""

    _PLACEHOLDER = re.compile(r"\{(\w+)\}")

    @classmethod
    def render(cls, template: str, context: SlackContext, *, command_text: str) -> str:
        """The first prompt for a Slack-started workspace.

        Unknown ``{names}`` stay as written, so a template that quotes JSON
        still renders. When the result is too long, the oldest messages go first,
        because the newest ones are what the person is asking about.

        Args:
            template: The configured prompt template.
            context: The conversation read from Slack.
            command_text: The request itself, mention and options removed.

        Returns:
            The prompt, at most ``PROMPT_LIMIT`` characters.
        """
        transcript = list(context.transcript)
        files = "\n".join(f"Too large to copy: {line}" for line in context.linked_files)
        while True:
            thread = "\n\n".join(transcript) or "(no earlier messages)"
            values = {
                "command_text": command_text,
                "author": context.author,
                "channel": context.channel,
                "permalink": context.permalink,
                "thread": f"{thread}\n\n{files}".strip(),
            }
            text = cls._fill(template, values)
            if len(text) <= PROMPT_LIMIT or not transcript:
                return text[:PROMPT_LIMIT]
            transcript.pop(0)

    @classmethod
    def _fill(cls, template: str, values: dict[str, str]) -> str:
        """Replace known ``{names}``; leave every other brace as written."""
        return cls._PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


__all__ = ["SlackContext", "SlackContextReader", "SlackPrompt"]
