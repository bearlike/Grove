"""The one module that talks to Slack: Web API calls and the Socket Mode connection.

Everything Slack-shaped crosses here and nowhere else. Calls raise
:class:`SlackError`; callers decide whether a failure matters (a lost reaction
does not, a failed thread read costs context, never a workspace).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.client import BaseSocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from grove.core.errors import GroveError
from grove.core.slack.events import SlackEvent, SlackFile, SlackMessage
from grove.core.slack.vocabulary import MARKDOWN_CHUNK, SlackEvents

# Slack answers these when a reaction is already in the state asked for.
_REACTION_NOOPS = frozenset({"already_reacted", "no_reaction"})


class SlackError(GroveError):
    """A Slack call failed or Slack refused it."""


class SlackGateway:
    """Slack's Web API and Socket Mode behind the few verbs Grove needs.

    Holds the two tokens' clients and small name caches. Thread-safe: the Web
    client is stateless per call, and the caches tolerate a racing duplicate
    lookup.
    """

    def __init__(self, *, bot_token: str, app_token: str, timeout: float = 15.0) -> None:
        """Build the clients. Nothing connects until :meth:`connect`.

        Args:
            bot_token: The ``xoxb-`` token every Web API call uses.
            app_token: The ``xapp-`` token Socket Mode connects with.
            timeout: Seconds one Web API or download call may take.
        """
        self._bot_token = bot_token
        self._timeout = timeout
        self._web = WebClient(token=bot_token, timeout=int(timeout))
        self._socket = SocketModeClient(app_token=app_token, web_client=self._web)
        self._bot_user_id = ""
        self._names: dict[str, str] = {}
        self._channels: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def bot_user_id(self) -> str:
        """The app's own member id, known once :meth:`connect` has run."""
        return self._bot_user_id

    # ─── connection ─────────────────────────────────────────────────────────

    def connect(self, on_event: Callable[[SlackEvent], None]) -> None:
        """Identify the app, then open the Socket Mode connection.

        Every envelope is acknowledged before it is read, because Slack resends
        one that is not acknowledged within three seconds. ``on_event`` must
        return quickly; it runs on the socket client's own threads.

        Raises:
            SlackError: The tokens were refused or the connection failed.
        """
        self._bot_user_id = str(self._call("auth_test").get("user_id", ""))

        def listen(client: BaseSocketModeClient, request: SocketModeRequest) -> None:
            client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
            event = self._event_of(request)
            if event is not None:
                on_event(event)

        self._socket.socket_mode_request_listeners.append(listen)
        try:
            self._socket.connect()
        except Exception as exc:
            raise SlackError(f"Slack Socket Mode connection failed: {exc}") from exc

    def close(self) -> None:
        """Close the Socket Mode connection."""
        self._socket.close()

    def _event_of(self, request: SocketModeRequest) -> SlackEvent | None:
        """The person's message inside one envelope, or ``None``."""
        payload: dict[str, Any] = request.payload or {}
        if request.type == SlackEvents.EVENTS_API:
            return SlackEvent.from_event(payload.get("event") or {}, bot_user_id=self._bot_user_id)
        if request.type == SlackEvents.INTERACTIVE:
            return SlackEvent.from_shortcut(payload)
        return None

    # ─── writes ─────────────────────────────────────────────────────────────

    def post(self, channel: str, thread_ts: str, text: str) -> None:
        """Post ``text`` as markdown into a thread, split when it is too long."""
        for start in range(0, max(len(text), 1), MARKDOWN_CHUNK):
            chunk = text[start : start + MARKDOWN_CHUNK]
            self._call(
                "chat_postMessage",
                channel=channel,
                thread_ts=thread_ts,
                text=chunk,
                blocks=[{"type": "markdown", "text": chunk}],
            )

    def react(self, channel: str, ts: str, name: str, *, on: bool = True) -> None:
        """Add (or with ``on=False`` remove) one reaction. Already so is not an error."""
        try:
            self._call(
                "reactions_add" if on else "reactions_remove",
                channel=channel,
                timestamp=ts,
                name=name,
            )
        except SlackError as exc:
            if not any(code in str(exc) for code in _REACTION_NOOPS):
                raise

    # ─── reads ──────────────────────────────────────────────────────────────

    def thread(self, channel: str, root_ts: str) -> list[SlackMessage]:
        """Every message in a thread, root first.

        shortcut: reads one page (up to 1,000 messages); page with ``cursor`` if a
        thread ever outgrows it.
        """
        reply = self._call("conversations_replies", channel=channel, ts=root_ts, limit=1000)
        return [SlackMessage.from_payload(m) for m in reply.get("messages") or []]

    def history(self, channel: str, *, before_ts: str, limit: int) -> list[SlackMessage]:
        """Up to ``limit`` channel messages before ``before_ts``, oldest first."""
        if limit <= 0:
            return []
        reply = self._call("conversations_history", channel=channel, latest=before_ts, limit=limit)
        return [SlackMessage.from_payload(m) for m in reversed(reply.get("messages") or [])]

    def user_name(self, user_id: str) -> str:
        """A member's display name, or the id when Slack will not say."""
        if user_id not in self._names:
            try:
                profile = self._call("users_info", user=user_id).get("user") or {}
                shown = (profile.get("profile") or {}).get("display_name") or profile.get(
                    "real_name"
                )
            except SlackError:
                shown = None
            with self._lock:
                self._names[user_id] = str(shown or user_id)
        return self._names[user_id]

    def channel_name(self, channel: str) -> str:
        """A channel's name, or its id for a DM or when Slack will not say."""
        if channel not in self._channels:
            try:
                info = self._call("conversations_info", channel=channel).get("channel") or {}
                shown = info.get("name")
            except SlackError:
                shown = None
            with self._lock:
                self._channels[channel] = str(shown or channel)
        return self._channels[channel]

    def permalink(self, channel: str, ts: str) -> str:
        """A link to one message, or empty when Slack will not give one."""
        try:
            return str(self._call("chat_getPermalink", channel=channel, message_ts=ts)["permalink"])
        except (SlackError, KeyError):
            return ""

    def download(self, file: SlackFile, *, max_bytes: int) -> bytes | None:
        """A shared file's bytes, or ``None`` when it is larger than ``max_bytes``.

        Raises:
            SlackError: The download failed.
        """
        if file.size > max_bytes:
            return None
        # The bot token rides this request, so it only ever goes to Slack itself.
        host = urlparse(file.url).hostname or ""
        if not host.endswith(".slack.com"):
            raise SlackError(f"refusing to download {file.name} from {host or 'an unknown host'}")
        headers = {"Authorization": f"Bearer {self._bot_token}"}
        try:
            response = httpx.get(file.url, headers=headers, timeout=self._timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SlackError(f"could not download {file.name}: {exc}") from exc
        return response.content if len(response.content) <= max_bytes else None

    # ─── the call edge ──────────────────────────────────────────────────────

    def _call(self, method: str, **kwargs: Any) -> Any:
        """One Web API call, with Slack's error narrowed to :class:`SlackError`."""
        try:
            return getattr(self._web, method)(**kwargs)
        except SlackApiError as exc:
            code = exc.response.get("error", "") if exc.response is not None else ""
            raise SlackError(f"Slack {method} failed: {code or exc}") from exc
        except Exception as exc:
            logger.debug("slack {} transport failure: {}", method, exc)
            raise SlackError(f"Slack {method} failed: {exc}") from exc


__all__ = ["SlackError", "SlackGateway"]
