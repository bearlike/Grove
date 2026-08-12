"""Grove in the OTLP path: a pure transform core, and a thin ASGI shell over it.

**The core is a function of spans, and that is the whole design.**
:class:`TraceGateway` takes ``ResourceSpans`` in and hands ``ResourceSpans``
out, with no socket, no clock of its own and no config lookup — ``now`` is a
parameter. Everything difficult (the batch race, the spawn tree, the
vocabulary) is therefore testable from a captured protobuf payload with no
server running, which is the only way the hard parts get tested at all.

**Deployment is configuration, not a second codebase.** :func:`build_receiver_app`
returns a standalone ASGI app: the daemon mounts it today, and the same module
runs as its own process behind whatever an organisation already scales when one
host stops being enough. Building it as daemon routes would have bought the
first and forfeited the second, and there is no cheap way back.

Concurrency, per the rules in ``src/grove/core/CLAUDE.md``:

* The request handler decodes nothing and transforms nothing. It parses the
  protobuf (cheap, bounded by the body it already read) and offers the batch to
  a **bounded** queue.
* Transform work runs on its **own bounded pool** with named threads — never
  the default executor, which is the render path. A telemetry spike that
  starved that pool would present to a user as a frozen dashboard, and nothing
  in the symptom would point at telemetry.
* A full queue **sheds and says so once per window**. Telemetry backpressure
  must never become agent backpressure, and a per-drop log at a thousand spans
  a second is its own outage.
* Shedding is reported honestly in the OTLP response's ``partial_success``
  rather than behind a bare 200, so a sender's own metrics can see it.

Importing this module needs the optional ``telemetry`` extra for
``opentelemetry-proto``; the FastAPI import is deferred into
:func:`build_receiver_app` so the pure core stays reachable on a daemon-less
install.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Final, Protocol

from loguru import logger
from opentelemetry.proto.common.v1.common_pb2 import InstrumentationScope
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans

from grove.core.telemetry._grouping import ReleasedTrace, TraceBuffer
from grove.core.telemetry._otlp import SpanEnvelope, repack
from grove.core.telemetry.claude_code import ClaudeCodeTransform

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from fastapi import FastAPI

DEFAULT_QUEUE_CAPACITY: Final = 256
"""Batches (not spans) the ingest queue will hold.

Sized in batches because that is the unit a sender retries. At the measured
export interval one agent contributes a batch every few seconds, so this is
several hundred concurrent agents' worth of slack before anything sheds — and
the bound matters more than the number, since an unbounded queue converts a
telemetry spike into an OOM that takes the daemon with it.
"""

DEFAULT_WORKERS: Final = 2
"""Transform threads. The work is CPU-bound protobuf walking under the GIL, so
more threads buy latency smoothing rather than throughput; the pool exists to
keep that work OFF the event loop, not to parallelise it."""

SHED_LOG_WINDOW: Final = timedelta(seconds=60)
"""How often a shedding receiver is allowed to say so. One line per window
carrying the window's count — a line per drop is indistinguishable from the
outage it is reporting."""


class SourceTransform(Protocol):
    """One agent vocabulary, normalized onto Grove's canonical shape.

    Two methods because claiming and transforming are separate decisions: a
    gateway serving a mixed fleet must be able to leave Codex's spans (and
    Grove's own) untouched rather than guess at them, and a transform that
    decided both at once could only signal "not mine" by returning its input
    unchanged — indistinguishable from "mine, and nothing needed doing".
    """

    @staticmethod
    def claims(resource: Resource, scope: InstrumentationScope) -> bool:
        """Whether spans under this resource/scope are this source's."""

    def apply(self, released: ReleasedTrace) -> tuple[SpanEnvelope, ...]:
        """Rewrite one whole trace. Called once per release, never per batch."""


class ResourceSpansSink(Protocol):
    """Where transformed spans go next.

    Deliberately NOT :class:`grove.core.trace.SpanSink`, which speaks
    :class:`~grove.core.trace.SpanRecord`. What leaves this gateway is OTLP that
    arrived as OTLP: re-modelling it into records and back would discard every
    attribute Grove has no opinion about, which is most of them.
    """

    def __call__(self, resource_spans: Sequence[ResourceSpans]) -> None:
        """Forward a batch. Best-effort — must never raise into the caller."""


@dataclass(slots=True, frozen=True)
class GatewayOutcome:
    """What one call to :meth:`TraceGateway.accept` produced.

    ``held`` is the number of spans still waiting for their root, and it is the
    field to watch: a steadily climbing hold means the age bound is short for
    the fleet's turns, while a hold that never grows means roots are arriving
    first and the buffer is costing latency for nothing.
    """

    resource_spans: tuple[ResourceSpans, ...]
    accepted_spans: int
    held: int


class TraceGateway:
    """The pure core: spans in, canonical spans out.

    Holds the group-by-trace buffer and the registered source transforms, and
    owns exactly one decision — *which transform, if any, gets this trace*.
    That dispatch happens once per RELEASE rather than once per span, because a
    transform that can only see one batch cannot resolve a parent that has not
    arrived yet, which is the defect the whole tier exists to fix.

    Not thread-safe by design. The shell runs it on a single worker so the
    buffer needs no lock; a deployment wanting more workers shards by trace id
    across gateways rather than locking one.
    """

    def __init__(
        self,
        *,
        transforms: Sequence[SourceTransform] | None = None,
        buffer: TraceBuffer | None = None,
    ) -> None:
        self._transforms = tuple(transforms) if transforms is not None else (ClaudeCodeTransform(),)
        self._buffer = buffer if buffer is not None else TraceBuffer()

    def accept(self, resource_spans: Sequence[ResourceSpans], *, now: datetime) -> GatewayOutcome:
        """Take one export batch; return whatever became ready to forward."""
        envelopes = SpanEnvelope.unpack(resource_spans)
        released = self._buffer.offer(envelopes, now=now)
        return GatewayOutcome(
            resource_spans=self._transform(released),
            accepted_spans=len(envelopes),
            held=self._buffer.held_spans,
        )

    def drain(self, *, now: datetime, force: bool = False) -> GatewayOutcome:
        """Release traces whose root never came (``force``: everything).

        The shell calls this on an idle tick and once at shutdown, which is what
        keeps a turn whose root was lost to a crashed agent from sitting in
        memory until the process ends.
        """
        released = self._buffer.drain(now=now, force=force)
        return GatewayOutcome(
            resource_spans=self._transform(released),
            accepted_spans=0,
            held=self._buffer.held_spans,
        )

    def _transform(self, released: Sequence[ReleasedTrace]) -> tuple[ResourceSpans, ...]:
        """Run each released trace through the transform that claims it.

        A trace no transform claims is forwarded VERBATIM. That is the property
        that makes this safe to put in front of a mixed fleet: an agent Grove
        has never been taught about keeps exporting exactly what it exported
        before, and the gateway is a proxy rather than a filter.
        """
        envelopes: list[SpanEnvelope] = []
        for trace in released:
            if not trace.envelopes:
                continue
            transform = self._claimant(trace)
            if transform is None:
                envelopes.extend(trace.envelopes)
                continue
            if trace.reason != "root":
                logger.debug(
                    "otlp gateway released trace {} on {} with {} spans and no root; "
                    "hierarchy left as received",
                    trace.trace_id.hex(),
                    trace.reason,
                    len(trace.envelopes),
                )
            envelopes.extend(transform.apply(trace))
        return tuple(repack(envelopes))

    def _claimant(self, trace: ReleasedTrace) -> SourceTransform | None:
        """The first transform claiming any span in this trace.

        Any, not all: one trace comes from one exporter, and asking every span
        would only make a mixed batch (which cannot happen) fail closed in a way
        no test could reach.
        """
        first = trace.envelopes[0]
        return next(
            (t for t in self._transforms if t.claims(first.resource, first.scope)),
            None,
        )


class _ShedCounter:
    """Count what the queue refused, and say so at most once per window.

    Its own class because "count" and "when may I speak" are one rule with one
    piece of state, and a bare counter beside a bare timestamp is how a log
    line comes to report a count that has already been reset.
    """

    def __init__(self, *, window: timedelta = SHED_LOG_WINDOW) -> None:
        self._window = window
        self._total = 0
        self._since_report = 0
        self._last_report: datetime | None = None

    @property
    def total(self) -> int:
        """Spans shed over the process's life."""
        return self._total

    def record(self, spans: int, *, now: datetime) -> None:
        """Note a shed batch, emitting the window's line when one is due."""
        self._total += spans
        self._since_report += spans
        if self._last_report is not None and now - self._last_report < self._window:
            return
        self._last_report = now
        logger.warning(
            "otlp receiver queue full: shed {} spans in the last window ({} total). "
            "Telemetry is being dropped to keep it from becoming agent backpressure.",
            self._since_report,
            self._total,
        )
        self._since_report = 0


class OtlpIngest:
    """The bounded queue and bounded pool between the loop and the transform.

    Nothing here transforms anything: :meth:`submit` is awaited by a request
    handler and must return in microseconds, so it does one non-blocking put and
    reports what it could not take. The worker owns the gateway and hands each
    batch to a pool thread, which is where the protobuf walking actually
    happens.
    """

    def __init__(
        self,
        *,
        gateway: TraceGateway | None = None,
        sink: ResourceSpansSink | None = None,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
        workers: int = DEFAULT_WORKERS,
        idle_drain: timedelta = timedelta(seconds=5),
    ) -> None:
        self._gateway = gateway if gateway is not None else TraceGateway()
        self._sink = sink
        self._queue: asyncio.Queue[Sequence[ResourceSpans]] = asyncio.Queue(maxsize=queue_capacity)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="grove-otlp")
        self._idle_drain = idle_drain
        self._shed = _ShedCounter()
        self._worker: asyncio.Task[None] | None = None

    @property
    def gateway(self) -> TraceGateway:
        """The transform core this ingest feeds.

        Public because the pool boundary is the thing worth asserting on from
        outside — a test proving the transform never runs on the event loop has
        to wrap this object's methods, and reaching through a private name would
        make that patch silently no-op the day the field is renamed.
        """
        return self._gateway

    @property
    def shed_spans(self) -> int:
        """Spans refused for want of queue room. The receiver's honesty metric."""
        return self._shed.total

    async def submit(self, resource_spans: Sequence[ResourceSpans], *, spans: int) -> int:
        """Enqueue a batch; return how many spans were REFUSED (0 on success).

        ``put_nowait`` rather than ``await put``: awaiting a full queue is
        exactly the backpressure this must not apply, since the sender blocking
        on it is an agent doing real work for a user.

        Self-starting, because **Starlette does not run a MOUNTED app's
        lifespan** — a receiver that waited to be started would accept every
        request and consume none of them when mounted, which is the deployment
        that ships first. :meth:`start` is idempotent, so the standalone
        lifespan and this call cannot both create a worker.
        """
        await self.start()
        try:
            self._queue.put_nowait(resource_spans)
        except asyncio.QueueFull:
            self._shed.record(spans, now=datetime.now(UTC))
            return spans
        return 0

    async def start(self) -> None:
        """Begin consuming. Idempotent, so a mount can call it unconditionally."""
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="grove-otlp-ingest")

    async def aclose(self) -> None:
        """Stop consuming and release the pool without waiting on in-flight work.

        A shutdown that blocked on the pool would let a slow export hold the
        daemon open, and the work in flight is telemetry — the one thing whose
        loss must never delay anything else. Whatever is still buffered is
        drained first (cheap, in-process) so a clean stop does not silently
        discard a turn.
        """
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        self._forward(self._gateway.drain(now=datetime.now(UTC), force=True))
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def _run(self) -> None:
        """Pull batches, transform them off-loop, and drain on an idle tick."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                batch = await asyncio.wait_for(
                    self._queue.get(), timeout=self._idle_drain.total_seconds()
                )
            except TimeoutError:
                await self._offload(loop, partial(self._drain_now))
                continue
            try:
                # `partial`, not a lambda: a lambda closes over `batch` by NAME
                # and this loop rebinds it, so the pool could transform whichever
                # batch happened to be current when the thread got scheduled.
                await self._offload(loop, partial(self._accept_now, batch))
            finally:
                self._queue.task_done()

    async def _offload(
        self, loop: asyncio.AbstractEventLoop, work: Callable[[], GatewayOutcome]
    ) -> None:
        """Run one gateway call on the transform pool and forward its output.

        ``run_in_executor`` with an explicit pool rather than ``asyncio.to_thread``,
        which can only target the DEFAULT executor — and the default executor is
        the render path. The extra line is the whole point of the call.

        Best-effort by contract: a transform that raises costs its own batch and
        nothing else. Letting it escape would kill the worker task and turn one
        malformed payload into a permanently deaf receiver.
        """
        try:
            outcome = await loop.run_in_executor(self._pool, work)
        except Exception as exc:
            logger.debug("otlp gateway transform failed; batch dropped: {}", exc)
            return
        self._forward(outcome)

    def _accept_now(self, batch: Sequence[ResourceSpans]) -> GatewayOutcome:
        """Pool-side entry: the clock is read on the worker, never on the loop."""
        return self._gateway.accept(batch, now=datetime.now(UTC))

    def _drain_now(self) -> GatewayOutcome:
        """Pool-side idle tick — releases traces whose root never arrived."""
        return self._gateway.drain(now=datetime.now(UTC))

    def _forward(self, outcome: GatewayOutcome) -> None:
        """Hand transformed spans to the sink, swallowing its failures."""
        if self._sink is None or not outcome.resource_spans:
            return
        try:
            self._sink(outcome.resource_spans)
        except Exception as exc:
            logger.debug(
                "otlp gateway sink rejected {} groups: {}", len(outcome.resource_spans), exc
            )


def build_receiver_app(*, ingest: OtlpIngest | None = None) -> FastAPI:
    """The OTLP/HTTP receiver as a standalone ASGI app.

    Mountable (the daemon runtime) and runnable on its own (the standalone one)
    from the same object — see the module docstring.

    A one-line deferral onto :mod:`grove.core.telemetry._asgi`, which owns the
    routes because FastAPI must be imported at ITS module scope to resolve the
    handlers' annotations. Re-exported here so the receiver has one public entry
    point and callers never have to know a private module exists.
    """
    from grove.core.telemetry._asgi import build_receiver_app as _build  # noqa: PLC0415

    return _build(ingest=ingest)


__all__ = [
    "DEFAULT_QUEUE_CAPACITY",
    "DEFAULT_WORKERS",
    "GatewayOutcome",
    "OtlpIngest",
    "ResourceSpansSink",
    "SourceTransform",
    "TraceGateway",
    "build_receiver_app",
]
