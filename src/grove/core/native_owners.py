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
from dataclasses import dataclass, field
from typing import Literal

from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.errors import GroveError


class OwnerUnavailable(GroveError):
    """A control could not be queued, with a code the daemon maps to a status."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# Everything a worker can be told to do. `steer` submits text as the next user
# input; the rest go to the provider's own control channel.
ControlOp = Literal["steer", "interrupt", "set_model", "answer", "compact", "command"]


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
        )
        self._bindings[identity.address] = binding
        return binding

    def unregister(self, binding: OwnerBinding) -> None:
        if self._bindings.get(binding.identity.address) is not binding:
            return
        binding.closed = True
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
        message_id = (
            f"own_{binding.sequence:08x}"
            if op == "steer" and binding.input_capacity is not None
            else ""
        )
        binding.enqueue(OwnerFrame(message_id, text, op=op))

    async def next_frame(self, binding: OwnerBinding) -> OwnerFrame:
        while not binding.closed:
            _, _, frame = await binding.queue.get()
            if frame is None or binding.closed:
                break
            if frame.op == "interrupt":
                binding.interrupt_pending = False
            if frame.message_id:
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
]
