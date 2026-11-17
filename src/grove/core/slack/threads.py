"""The durable map from Slack threads to the workspaces answering in them."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grove.core import paths
from grove.core.record_file import RecordCodec, RecordFile

_VERSION = 1


@dataclass(frozen=True, slots=True)
class SlackThread:
    """One Slack thread and the workspace it drives.

    Attributes:
        channel: The channel (or DM) id.
        root_ts: The thread's root message timestamp.
        workspace_id: The workspace answering in this thread.
        repo_root: That workspace's repository, to find its manager.
        muted: Whether plain thread replies are ignored until ``unmute``.
        ended: Whether the workspace is gone, so the next mention starts anew.
        spinner_ts: The message carrying the working reaction, or empty.
        last_reply: Id of the last agent reply posted, so none posts twice.
        asked: Answer-back id of the last question batch posted, so none posts twice.
        awaiting: How many questions in that batch still wait for a thread reply.
    """

    channel: str
    root_ts: str
    workspace_id: str
    repo_root: str
    muted: bool = False
    ended: bool = False
    spinner_ts: str = ""
    last_reply: str = ""
    asked: str = ""
    awaiting: int = 0

    @property
    def key(self) -> str:
        """The record key: one thread is one channel plus one root timestamp."""
        return self.key_of(self.channel, self.root_ts)

    @staticmethod
    def key_of(channel: str, root_ts: str) -> str:
        """The record key for a thread that may not have a record yet."""
        return f"{channel}:{root_ts}"


class SlackThreadLog:
    """Every Slack-started workspace's thread, as one checksummed records file.

    Survives a daemon restart, which is the point: a thread reply after a
    restart must steer the existing workspace, and an agent reply already posted
    must not post again. A damaged file is moved aside and the log starts empty;
    the cost is that old threads stop being steerable, never a daemon that fails
    to start.

    shortcut: ended rows are kept forever (one small row per Slack-started
    workspace); prune by age if a host ever accumulates enough to matter.
    """

    def __init__(self, path: Path | None = None) -> None:
        """Open the log.

        Args:
            path: Where the records live. ``None`` uses the state directory.
        """
        self._file: RecordFile[SlackThread] = RecordFile(
            path if path is not None else paths.slack_threads_path(),
            store="slack-threads",
            version=_VERSION,
            codec=RecordCodec(key=lambda t: t.key, encode=_encode, decode=_decode),
            on_unreadable="quarantine",
        )

    def get(self, channel: str, root_ts: str) -> SlackThread | None:
        """The thread's record, or ``None`` when no workspace was started in it."""
        return self._file.get(SlackThread.key_of(channel, root_ts))

    def live(self) -> dict[str, SlackThread]:
        """Threads whose workspace has not ended, keyed by workspace id."""
        return {t.workspace_id: t for t in self._file.records().values() if not t.ended}

    def put(self, thread: SlackThread) -> None:
        """Write one thread, replacing any earlier record of it."""
        self._file.mutate(lambda records: {**records, thread.key: thread})

    def update(self, key: str, change: Callable[[SlackThread], SlackThread]) -> SlackThread | None:
        """Apply ``change`` to the latest version of one record, under the file lock.

        Returns:
            The record as written, or ``None`` when there is no such thread.
        """
        written: list[SlackThread] = []

        def apply(records: Any) -> Any:
            current = records.get(key)
            if current is None:
                return None
            written.append(change(current))
            return {**records, key: written[0]}

        self._file.mutate(apply)
        return written[0] if written else None


def _encode(thread: SlackThread) -> dict[str, Any]:
    return dataclasses.asdict(thread)


def _decode(row: dict[str, Any]) -> SlackThread:
    return SlackThread(**row)


__all__ = ["SlackThread", "SlackThreadLog"]
