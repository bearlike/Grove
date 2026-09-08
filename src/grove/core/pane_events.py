"""One bounded, event-triggered terminal projection per live pane.

A control-mode reader provides only an edge: terminal bytes cannot reproduce the
rendered tmux grid.  Each edge therefore asks the existing snapshot seam for a
fresh ANSI grid.  That preserves scrollback, resizing and attributes without a
second terminal emulator or periodic ``capture-pane`` work.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, cast

try:
    import pty
except ImportError:  # pragma: no cover - Windows has no tmux or pty
    pty = None  # type: ignore[assignment]

from grove.core.admission import (
    Admission,
    AdmissionLimits,
    BoundedInbox,
    InboxClosed,
)


def _open_pty() -> tuple[int, int]:
    """Open a POSIX control terminal only on platforms tmux can support."""
    if pty is None:
        raise PaneEventsUnavailable("tmux control mode requires a POSIX pty")
    return pty.openpty()


@dataclass(frozen=True, slots=True)
class PaneKey:
    """The one owner coordinate: workspace plus its named runtime pane."""

    workspace_id: str
    pane: str = ""


@dataclass(frozen=True, slots=True)
class PaneSnapshot:
    """A rendered ANSI grid, or the explicit empty/unavailable pane state."""

    ansi: str | None
    taken_at: datetime | None

    @property
    def size_bytes(self) -> int:
        """Account for retained UTF-8 terminal state, not Python characters."""
        return len(self.ansi.encode()) if self.ansi is not None else 0


class PaneTrigger(StrEnum):
    """Reasons a rendered pane must be captured again."""

    INITIAL = "initial"
    OUTPUT = "output"
    RESIZE = "resize"


PaneCapture = Callable[[], Awaitable[PaneSnapshot]]
PaneSourceFactory = Callable[[], "PaneEventSource"]


class PaneEventsUnavailable(RuntimeError):
    """The runtime has no maintained pane-event transport."""


class PaneEventSource(Protocol):
    """A maintained edge stream for one pane; never a snapshot poller."""

    def events(self) -> AsyncIterator[PaneTrigger]: ...

    async def aclose(self) -> None: ...


class PaneSubscription:
    """One consumer's bounded projection from a shared pane owner."""

    def __init__(self, producer: _PaneProducer) -> None:
        self._producer = producer
        self._inbox: BoundedInbox[PaneSnapshot] = BoundedInbox(producer.subscriber_limits)
        self._closed = False

    async def events(self) -> AsyncGenerator[PaneSnapshot, None]:
        """Yield current state then coalesced changes until source termination."""
        self._inbox.bind()
        await self._producer.attach(self)
        try:
            while True:
                delivery = await self._inbox.take()
                try:
                    yield delivery.value
                finally:
                    self._inbox.complete(delivery)
                    self._producer.resume(self)
        except InboxClosed:
            return
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        """Release this viewer; the final viewer stops its maintained reader."""
        if self._closed:
            return
        self._closed = True
        self._inbox.close()
        await self._producer.detach(self)


class PaneEventHub:
    """Own at most one event reader per workspace/pane key.

    The hub is daemon-lifespan-owned.  Source factories are evaluated only for a
    new key, so a second browser attaches to the existing reader instead of
    creating another control client or capture subprocess.
    """

    def __init__(
        self,
        *,
        snapshot_limits: AdmissionLimits | None = None,
        subscriber_limits: AdmissionLimits | None = None,
    ) -> None:
        default_limits = AdmissionLimits(max_items=2, max_bytes=4 * 1024 * 1024)
        self._snapshot_limits = snapshot_limits or default_limits
        self._subscriber_limits = subscriber_limits or default_limits
        self._producers: dict[PaneKey, _PaneProducer] = {}
        self._closed = False

    def subscribe(
        self,
        key: PaneKey,
        *,
        capture: PaneCapture,
        source: PaneSourceFactory,
    ) -> PaneSubscription:
        """Join the keyed owner, or start exactly one new owner for it."""
        if self._closed:
            raise RuntimeError("pane event hub is closed")
        producer = self._producers.get(key)
        if producer is None:
            producer = _PaneProducer(
                key=key,
                capture=capture,
                source=source(),
                snapshot_limits=self._snapshot_limits,
                subscriber_limits=self._subscriber_limits,
                on_stopped=self._discard,
            )
            self._producers[key] = producer
            producer.start()
        return PaneSubscription(producer)

    async def aclose(self) -> None:
        """Stop every maintained reader without waiting for terminal I/O forever."""
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(*(producer.aclose() for producer in tuple(self._producers.values())))
        self._producers.clear()

    def _discard(self, key: PaneKey, producer: _PaneProducer) -> None:
        # A source may end while a replacement is being constructed.  Identity
        # prevents the old task from deleting that newer owner.
        if self._producers.get(key) is producer:
            del self._producers[key]


class _PaneProducer:
    """Own one source, one bounded rendered state, and many bounded viewers."""

    def __init__(
        self,
        *,
        key: PaneKey,
        capture: PaneCapture,
        source: PaneEventSource,
        snapshot_limits: AdmissionLimits,
        subscriber_limits: AdmissionLimits,
        on_stopped: Callable[[PaneKey, _PaneProducer], None],
    ) -> None:
        self.key = key
        self._capture = capture
        self._source = source
        self._snapshot_limits = snapshot_limits
        self.subscriber_limits = subscriber_limits
        # One running refresh plus one keyed trailing refresh.  An output burst
        # can never queue N captures, but an edge received during capture is not
        # lost: it becomes exactly one resnapshot after that capture finishes.
        self._triggers: BoundedInbox[PaneTrigger] = BoundedInbox(
            AdmissionLimits(max_items=2, max_bytes=2)
        )
        self._subscribers: set[PaneSubscription] = set()
        self._current: PaneSnapshot | None = None
        self._initial_capture: asyncio.Future[PaneSnapshot] | None = None
        self._stale: set[PaneSubscription] = set()
        self._task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False
        self._on_stopped = on_stopped

    def start(self) -> None:
        """Start source ingestion once, on the daemon event loop."""
        self._triggers.bind()
        self._task = asyncio.create_task(
            self._refresh(), name=f"grove-pane:{self.key.workspace_id}"
        )
        self._reader_task = asyncio.create_task(
            self._ingest(), name=f"grove-pane-reader:{self.key.workspace_id}"
        )
        self._offer_trigger(PaneTrigger.INITIAL)

    async def attach(self, subscriber: PaneSubscription) -> None:
        """Publish retained state, waiting for the shared initial capture if needed."""
        if self._closed:
            subscriber._inbox.close()
            return
        self._subscribers.add(subscriber)
        if self._current is not None:
            self._offer_snapshot(subscriber, self._current)
            return
        if self._initial_capture is not None:
            with suppress(asyncio.CancelledError):
                await asyncio.shield(self._initial_capture)
        if self._current is not None and not self._closed:
            self._offer_snapshot(subscriber, self._current)

    async def detach(self, subscriber: PaneSubscription) -> None:
        self._subscribers.discard(subscriber)
        self._stale.discard(subscriber)
        if not self._subscribers:
            await self.aclose()

    def resume(self, subscriber: PaneSubscription) -> None:
        """Resync a slow viewer only after it has released retained bytes."""
        if subscriber in self._stale and self._current is not None and not self._closed:
            self._stale.discard(subscriber)
            self._offer_snapshot(subscriber, self._current)

    def _offer_trigger(self, trigger: PaneTrigger) -> None:
        # Trigger bodies carry no terminal state.  Every trigger has the same
        # key so BoundedInbox replaces the pending one rather than buffering a
        # capture per byte/output notification.
        self._triggers.offer(trigger, size_bytes=1, key="resnapshot")

    async def _ingest(self) -> None:
        try:
            async for trigger in self._source.events():
                if self._closed:
                    return
                self._offer_trigger(trigger)
        finally:
            # A control transport disconnect ends subscribers.  EventSource
            # reconnects and creates one replacement owner; retaining a dead
            # owner would turn a temporary disconnect into permanent silence.
            await self.aclose()

    async def _refresh(self) -> None:
        try:
            while True:
                delivery = await self._triggers.take()
                try:
                    capture: asyncio.Future[PaneSnapshot] = asyncio.ensure_future(self._capture())
                    if delivery.value is PaneTrigger.INITIAL:
                        self._initial_capture = capture
                    snapshot = await capture
                    if delivery.value is PaneTrigger.INITIAL:
                        self._initial_capture = None
                    if snapshot.size_bytes > self._snapshot_limits.max_bytes:
                        # Never slice ANSI: cutting an SGR/control sequence
                        # corrupts rendering.  Empty is explicit, bounded and
                        # the next output/resize retries a whole resnapshot.
                        snapshot = PaneSnapshot(ansi=None, taken_at=None)
                    self._current = snapshot
                    for subscriber in tuple(self._subscribers):
                        self._offer_snapshot(subscriber, snapshot)
                finally:
                    self._triggers.complete(delivery)
        except InboxClosed:
            return
        finally:
            await self.aclose()

    def _offer_snapshot(self, subscriber: PaneSubscription, snapshot: PaneSnapshot) -> None:
        result = subscriber._inbox.offer(snapshot, size_bytes=snapshot.size_bytes, key="snapshot")
        if result in {Admission.FULL, Admission.TOO_LARGE}:
            # The owner continues ingesting regardless of a wedged writer.  A
            # viewer that missed a frame gets the newest retained whole grid as
            # soon as it releases the frame it is still holding.
            self._stale.add(subscriber)

    async def aclose(self) -> None:
        """Close source, readers and viewer queues exactly once."""
        if self._closed:
            return
        self._closed = True
        self._triggers.close()
        if self._initial_capture is not None:
            self._initial_capture.cancel()
        for subscriber in tuple(self._subscribers):
            subscriber._inbox.close()
        self._subscribers.clear()
        self._stale.clear()
        current = asyncio.current_task()
        for task in (self._task, self._reader_task):
            if task is not None and task is not current:
                task.cancel()
        await self._source.aclose()
        self._on_stopped(self.key, self)


class TmuxControlPaneSource:
    """Translate one tmux control client into edges for one exact pane.

    ``%output`` is raw terminal data, not a rendered grid, so it only triggers
    the existing ANSI ``capture-pane`` projection.  A control client attaches a
    *session* even when given a pane target; each output notification therefore
    carries a pane id and must be filtered or output in a sibling pane creates a
    needless capture.  A bare tmux uses a PTY and ``-CC``; a command prefix uses
    pipes and ``-C`` (the non-terminal protocol form) plus Docker's ``-i``.
    """

    _OUTPUT = re.compile(r"^%(?:extended-)?output (?P<pane>%[0-9]+)(?: |$)")
    _PANE = re.compile(r"^%[0-9]+$")

    def __init__(
        self,
        *,
        target: str,
        pane_id: str | None = None,
        command: tuple[str, ...] = ("tmux",),
    ) -> None:
        self._target = target
        self._pane_id = pane_id
        if pane_id is not None and self._PANE.fullmatch(pane_id) is None:
            raise PaneEventsUnavailable("tmux control events require a pane-id target")
        self._command = command
        self._process: asyncio.subprocess.Process | None = None
        self._transport: asyncio.Transport | None = None
        self._reader: asyncio.StreamReader | None = None
        self._master_fd: int | None = None
        self._closed = False

    async def events(self) -> AsyncIterator[PaneTrigger]:
        """Yield output/layout edges relevant to this source's exact pane."""
        await self._start()
        assert self._reader is not None and self._pane_id is not None
        while not self._closed:
            line = await self._reader.readline()
            if not line:
                return
            notification = line.decode(errors="replace").rstrip("\r\n")
            output = self._OUTPUT.match(notification)
            if output is not None:
                if output.group("pane") == self._pane_id:
                    yield PaneTrigger.OUTPUT
            elif notification.startswith("%layout-change "):
                yield PaneTrigger.RESIZE
            elif notification.startswith("%exit"):
                return

    async def _start(self) -> None:
        if self._closed:
            raise RuntimeError("pane control source is closed")
        if self._process is not None:
            return
        if self._pane_id is None:
            self._pane_id = await self._resolve_pane_id()
        if self._command == ("tmux",):
            await self._start_host()
        else:
            await self._start_prefixed()

    async def _resolve_pane_id(self) -> str:
        """Resolve the target once, before the maintained reader begins."""
        process = await asyncio.create_subprocess_exec(
            *self._command,
            "display-message",
            "-p",
            "-t",
            self._target,
            "#{pane_id}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        pane_id = stdout.decode().strip()
        if process.returncode != 0 or self._PANE.fullmatch(pane_id) is None:
            raise PaneEventsUnavailable("tmux cannot resolve the requested pane")
        return pane_id

    async def _start_host(self) -> None:
        master, slave = _open_pty()
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                "-CC",
                "attach-session",
                "-r",
                "-t",
                self._target,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
            )
        except BaseException:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        self._master_fd = master
        stream = os.fdopen(master, "rb", buffering=0)
        reader = asyncio.StreamReader(limit=128 * 1024)
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, stream)
        self._transport = cast(asyncio.Transport, transport)
        self._reader = reader

    async def _start_prefixed(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            *self._prefixed_control_command(),
            "-C",
            "attach-session",
            "-r",
            "-t",
            self._target,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        assert self._process.stdout is not None
        self._reader = self._process.stdout

    def _prefixed_control_command(self) -> tuple[str, ...]:
        """Insert ``-i`` at Docker exec's option boundary, before its container id."""
        command = list(self._command)
        if len(command) >= 2 and command[0].endswith("docker") and command[1] == "exec":
            # ContainerRuntimeState.exec_argv emits ``docker exec [-u user]
            # <full-id> <tmux>``.  Docker parses flags only before the id, so
            # append to that option run rather than before the tmux binary.
            index = 2
            if command[index : index + 1] == ["-u"]:
                index += 2
            command.insert(index, "-i")
        return tuple(command)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        process = self._process
        self._process = None
        if process is not None and process.returncode is None:
            process.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=0.5)
            if process.returncode is None:
                process.kill()
        self._reader = None
        self._master_fd = None
