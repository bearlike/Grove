"""The registry of connected native owner workers, and the frames they run.

A native workspace's agent is held by a Grove-owned worker process, which
connects to the daemon and waits for frames: text to submit, an interrupt, a
model switch, a slash command, an answer to a standing question. This module is
that registry and that queue — the daemon's *control plane* for the sessions it
owns.

**This is not the mailbox.** Peer mail used to live here because a delivered
message was one more frame on this queue, which made the queue look like a
messaging system; it is not. It is how a steer reaches a process that has no
terminal to type into, and `core/mailboxes.py` reaches it the same way every
other steer does — through ``WorkspaceManager.send_message``. Splitting the two
is what lets a mailbox message address an interactive terminal agent, which has
no owner here at all.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import ClassVar, Literal
from uuid import uuid4

from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.errors import GroveError


class OwnerUnavailable(GroveError):
    """A control could not be queued, with a code the daemon maps to a status."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Everything a worker can be told to do. `steer` submits text as the next user
# input; the rest go to the provider's own control channel.
ControlOp = Literal[
    "steer",
    "interrupt",
    "set_model",
    "set_effort",
    "answer",
    "compact",
    "command",
    "macro",
    "watch",
    "read",
]


@dataclass(slots=True, frozen=True)
class WorktreeRead:
    """What one command run in a worker's worktree answered.

    ``output`` is the combined stdout and stderr, cut by the worker at
    :attr:`OUTPUT_LIMIT` characters; ``returncode`` is ``-1`` for a command the
    worker killed at its deadline.
    """

    #: The most output one read carries back over the channel.
    OUTPUT_LIMIT: ClassVar[int] = 65_536

    returncode: int
    output: str


@dataclass(slots=True, frozen=True)
class OwnerIdentity:
    """One worker incarnation.

    The generation fences a worker that has been replaced since a caller last
    looked — the daemon's own bookkeeping, never something a peer supplies.
    """

    address: MailboxAddress
    generation: str


@dataclass(slots=True)
class OwnerFrame:
    """One unit of work the owner worker acts on.

    The daemon answered its caller on dispatch — the same "delivered, not
    confirmed" contract typing into a tmux pane has.
    """

    message_id: str
    text: str
    op: ControlOp = "steer"


@dataclass(slots=True)
class OwnerBinding:
    identity: OwnerIdentity
    provider_session_id: str
    queue: asyncio.PriorityQueue[tuple[int, int, OwnerFrame | None]]
    closed: bool = False
    submitted: set[str] = field(default_factory=set)
    interrupt_pending: bool = False
    sequence: int = 0
    input_capacity: int | None = None
    input_ids: set[str] = field(default_factory=set)
    acknowledged_inputs: dict[str, str] = field(default_factory=dict)
    # The worker declared it understands `watch`; an older one would queue an
    # unknown op as prompt text, so nothing live is ever sent to it.
    live: bool = False
    # The worker declared it serves `read`; the same reason as `live`.
    reads: bool = False
    pending_reads: dict[str, asyncio.Future[WorktreeRead]] = field(default_factory=dict)

    def enqueue(self, frame: OwnerFrame) -> None:
        # Cancellation has one reserved, coalesced slot; text remains FIFO.
        if self.input_capacity is not None and frame.op == "steer":
            self.input_ids.add(frame.message_id)
        priority = 0 if frame.op == "interrupt" else 1
        self.queue.put_nowait((priority, self.sequence, frame))
        self.sequence += 1

    def queue_full(self, capacity: int) -> bool:
        return self.queue.qsize() - int(self.interrupt_pending) >= capacity

    def input_full(self, capacity: int) -> bool:
        return self.queue_full(capacity) or (
            self.input_capacity is not None and len(self.input_ids) >= self.input_capacity
        )


class NativeOwnerRegistry:
    """Which workers are connected, and what each has been asked to do.

    Process-local state belonging to one daemon loop. A worker outlives the
    daemon, so a registration is dropped on shutdown and re-made on reconnect;
    only a workspace lifecycle edge invalidates one for real.
    """

    def __init__(self, *, queue_capacity: int = 16) -> None:
        self.queue_capacity = queue_capacity
        self._bindings: dict[MailboxAddress, OwnerBinding] = {}

    def register(
        self,
        identity: OwnerIdentity,
        provider_session_id: str,
        *,
        input_capacity: int | None = None,
        pending_input_ids: tuple[str, ...] = (),
        live: bool = False,
        reads: bool = False,
    ) -> OwnerBinding:
        if not provider_session_id:
            raise OwnerUnavailable("invalid_registration", "a native owner needs its session id")
        if identity.address in self._bindings:
            raise OwnerUnavailable(
                "already_registered", "this agent slot already has a native owner"
            )
        if pending_input_ids and (
            input_capacity is None or len(set(pending_input_ids)) > input_capacity
        ):
            raise OwnerUnavailable("backpressure", "invalid pending input reservation")
        binding = OwnerBinding(
            identity,
            provider_session_id,
            asyncio.PriorityQueue(self.queue_capacity + 1),
            input_capacity=input_capacity,
            input_ids=set(pending_input_ids),
            submitted=set(pending_input_ids),
            live=live,
            reads=reads,
        )
        self._bindings[identity.address] = binding
        return binding

    def unregister(self, binding: OwnerBinding) -> None:
        if self._bindings.get(binding.identity.address) is not binding:
            return
        binding.closed = True
        for waiting in binding.pending_reads.values():
            if not waiting.done():
                waiting.set_exception(
                    OwnerUnavailable("not_registered", "native owner went away mid-read")
                )
        binding.pending_reads.clear()
        while not binding.queue.empty():
            binding.queue.get_nowait()
        binding.interrupt_pending = False
        binding.queue.put_nowait((0, binding.sequence, None))
        del self._bindings[binding.identity.address]

    def invalidate(self, address: MailboxAddress) -> None:
        binding = self._bindings.get(address)
        if binding is not None:
            self.unregister(binding)

    def binding(self, identity: OwnerIdentity) -> OwnerBinding:
        binding = self._bindings.get(identity.address)
        if binding is None or binding.closed or binding.identity != identity:
            raise OwnerUnavailable("not_registered", "agent has no current native binding")
        return binding

    def owner_for(self, workspace_id: str) -> OwnerBinding | None:
        """The live owner of a workspace's primary agent, or ``None``."""
        binding = self._bindings.get(MailboxAddress(workspace_id=workspace_id))
        return None if binding is None or binding.closed else binding

    def control(self, workspace_id: str, op: ControlOp, text: str = "") -> None:
        """Queue one operator control for the workspace's native owner.

        Raises when no owner is connected: the daemon maps that to the same 409
        a missing pane gets, because a respawn is the remedy in both cases. No
        receipt — the owner relays the frame to the provider's control channel
        and the provider's answer surfaces in its own stream, exactly like a
        keystroke typed into a pane.
        """
        binding = self.owner_for(workspace_id)
        if binding is None:
            raise OwnerUnavailable("not_registered", "workspace has no connected native owner")
        if op == "interrupt":
            if binding.interrupt_pending:
                return
            binding.interrupt_pending = True
        elif binding.queue_full(self.queue_capacity) or (
            op == "steer" and binding.input_full(self.queue_capacity)
        ):
            raise OwnerUnavailable("backpressure", "native owner is not draining its queue")
        # An input id survives reconnect in the worker's pending receipt set;
        # this daemon-local sequence does not. A fresh daemon would otherwise
        # reuse an id and the worker would correctly de-duplicate its new steer.
        message_id = (
            f"own_{uuid4().hex}" if op == "steer" and binding.input_capacity is not None else ""
        )
        binding.enqueue(OwnerFrame(message_id, text, op=op))

    def watch(self, workspace_id: str, *, on: bool) -> bool:
        """Tell the workspace's owner to start or stop publishing its live step.

        Coalesced and backpressure-free on purpose: a watch toggle is display
        state, so a full queue drops it rather than refusing a viewer, and the
        next toggle re-states the whole truth. ``False`` when no live-capable
        owner is connected — the viewer then simply sees no pulse.
        """
        binding = self.owner_for(workspace_id)
        if binding is None or not binding.live or binding.queue.full():
            return False
        binding.enqueue(OwnerFrame("", "on" if on else "off", op="watch"))
        return True

    async def read(self, workspace_id: str, argv: list[str], *, timeout: float) -> WorktreeRead:
        """Run ``argv`` in the owner's own worktree and wait for what it answered.

        The worktree lives wherever the worker does, so this is the one road
        to it for a workspace whose files are not on this host.

        Raises:
            OwnerUnavailable: No owner that serves reads is connected, its queue
                is full, it went away, or it did not answer within ``timeout``.
        """
        return await self._ask(workspace_id, {"argv": argv}, timeout=timeout)

    async def tail(self, workspace_id: str, lines: int, *, timeout: float) -> WorktreeRead:
        """The last ``lines`` of the owner's own session transcript.

        The worker finds the file itself, in its own namespace, because only it
        knows where its provider wrote it. An empty ``output`` with status 0 is
        a session with no transcript yet.

        Raises:
            OwnerUnavailable: As for :meth:`read`.
        """
        return await self._ask(workspace_id, {"tail": lines}, timeout=timeout)

    async def _ask(
        self, workspace_id: str, request: dict[str, object], *, timeout: float
    ) -> WorktreeRead:
        binding = self.owner_for(workspace_id)
        if binding is None or not binding.reads:
            raise OwnerUnavailable("not_registered", "workspace has no owner that serves reads")
        if binding.queue_full(self.queue_capacity):
            raise OwnerUnavailable("backpressure", "native owner is not draining its queue")
        request_id = f"read_{uuid4().hex}"
        answer: asyncio.Future[WorktreeRead] = asyncio.get_running_loop().create_future()
        binding.pending_reads[request_id] = answer
        frame = json.dumps({**request, "timeout": timeout})
        binding.enqueue(OwnerFrame(request_id, frame, op="read"))
        try:
            return await asyncio.wait_for(answer, timeout)
        except TimeoutError as exc:
            raise OwnerUnavailable("timeout", "native owner did not answer the read") from exc
        finally:
            binding.pending_reads.pop(request_id, None)

    def resolve_read(self, binding: OwnerBinding, request_id: str, result: WorktreeRead) -> None:
        """Complete one pending read; an answer nobody is waiting for is dropped.

        A late answer after the caller timed out is ordinary, so it is not an
        error; a request id this owner was never sent is.
        """
        self.binding(binding.identity)
        answer = binding.pending_reads.get(request_id)
        if answer is None:
            if not request_id.startswith("read_"):
                raise OwnerUnavailable("unknown_submission", "not a read this owner was sent")
            return
        if not answer.done():
            answer.set_result(result)

    async def next_frame(self, binding: OwnerBinding) -> OwnerFrame:
        while not binding.closed:
            _, _, frame = await binding.queue.get()
            if frame is None or binding.closed:
                break
            if frame.op == "interrupt":
                binding.interrupt_pending = False
            if frame.message_id and frame.op != "read":
                binding.submitted.add(frame.message_id)
            return frame
        raise OwnerUnavailable("not_registered", "native binding was closed")

    def acknowledge(self, binding: OwnerBinding, message_id: str, *, stage: str) -> None:
        """Release one input credit once the worker reports its submission.

        A response can be lost after the credit is released, so a repeated
        acknowledgement is idempotent rather than a second release.
        """
        self.binding(binding.identity)
        if message_id in binding.acknowledged_inputs:
            return
        if message_id not in binding.submitted:
            raise OwnerUnavailable(
                "unknown_submission", "submission does not belong to this native owner"
            )
        if stage not in {"queued", "delivered", "unknown", "rejected"}:
            raise OwnerUnavailable("invalid_receipt", "invalid native submission stage")
        binding.submitted.discard(message_id)
        binding.input_ids.discard(message_id)
        if binding.input_capacity is not None:
            binding.acknowledged_inputs[message_id] = stage
            if len(binding.acknowledged_inputs) > binding.input_capacity:
                del binding.acknowledged_inputs[next(iter(binding.acknowledged_inputs))]


__all__ = [
    "ControlOp",
    "NativeOwnerRegistry",
    "OwnerBinding",
    "OwnerFrame",
    "OwnerIdentity",
    "OwnerUnavailable",
    "WorktreeRead",
]
