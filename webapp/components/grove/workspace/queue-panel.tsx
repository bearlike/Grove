"use client";

import { useState } from "react";
import { MessagesSquareIcon } from "lucide-react";

import { CardDisclosure, CardShell } from "@/components/grove/card";
import { RelativeTime } from "@/components/grove/relative-time";
import type { WorkspaceQueueView } from "@/lib/grove/api";

/**
 * The harness's own steer queue — messages typed while the agent was busy,
 * held until the harness can inject them.
 *
 * Grove reads this route rather than keeping a queue of its own sends, because
 * the real queue is BIDIRECTIONAL: a message Grove sent and the harness queued
 * belongs here, and so does one typed straight into the pane, which Grove never
 * sent at all and could never reconstruct from its own record. So this card
 * only ever renders `queue.messages` as given — never a client-side ledger.
 *
 * It sits directly below the plan card in the thread's viewport footer, and is
 * composed exactly the same way: `CardShell` + `CardDisclosure`.
 *
 * OPEN by default, which is where it differs from the plan card, and the
 * difference is the whole reason this card exists. The plan card is always
 * present and often long, so collapsing it protects the transcript; this card
 * renders ONLY when something is actually waiting.
 *
 * **It starts CLOSED, reversing an earlier decision that is worth recording
 * rather than quietly overwriting.** The original argument was that the card
 * exists to answer "I send a steer message and never see it", so starting shut
 * would reproduce that complaint. That reasoning conflated two things the card
 * does: it *announces* a backlog, and it *lists* one. The announcement survives
 * a closed disclosure intact — the card only mounts when something is waiting,
 * and the count rides the summary header — so a person still sees that their
 * message landed and how many are ahead of it. What a closed card withholds is
 * only the message TEXT, which is the part nobody needs on arrival.
 *
 * Opening on its own was the real defect: a queue that expands whenever
 * something arrives moves the composer down the page at the least welcome
 * moment, which is precisely while the user is typing the next message.
 */
export function QueuePanel({
  queue,
  defaultOpen = false,
}: {
  queue: WorkspaceQueueView;
  /** Initial disclosure state. The same name and default Radix's own
   * `Collapsible` uses, so a caller that wants the list open on mount can say
   * so without this component growing a second vocabulary for it. */
  defaultOpen?: boolean;
}) {
  // Seeded once and never keyed on the queue: a NEW arrival must not reopen a
  // card the user shut, which is the same "a deliberate choice outranks a
  // default" rule the workspace tab selection follows.
  const [open, setOpen] = useState(defaultOpen);

  // Two separate guards, not one `messages.length === 0` check. The wire
  // contract is explicit that these are different claims — "nothing waiting"
  // against "Grove cannot see this harness's queue" — even though an
  // unsupported harness always reports an empty tuple too, and even though
  // both guards land on the same "render no card" outcome below. Collapsing
  // them into one check would make that distinction invisible to the next
  // reader of this file, which is the thing the contract asked us not to do.
  if (!queue.supported) return null;
  if (queue.messages.length === 0) return null;

  const count = queue.messages.length;

  return (
    <CardShell className="surface-raised" data-testid="queue-card" data-collapsed={!open}>
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        data-testid="queue-toggle"
        header
        summary={
          <>
            <MessagesSquareIcon aria-hidden className="size-4 shrink-0 text-content-tertiary" />
            <span className="shrink-0 text-sm font-medium">Queued</span>
            <span className="ms-auto shrink-0 text-xs text-content-tertiary tabular-nums">
              {count} {count === 1 ? "message" : "messages"} waiting
            </span>
          </>
        }
      >
        {/* Capped rather than free, same reasoning as the plan card: an
            expanded card that eats the viewport pushes the composer off-screen. */}
        <ul className="max-h-56 overflow-y-auto px-3 pt-1 pb-3" data-testid="queue-list">
          {queue.messages.map((message) => (
            <li
              key={message.position}
              className="flex min-w-0 items-baseline gap-3 py-1.5 first:pt-0 last:pb-0"
              data-testid="queue-message"
            >
              {/* A SENTENCE wraps rather than truncates: this is the reader's
                  own instruction, and clipping it would lose the part it was
                  sent for. */}
              <span className="min-w-0 flex-1 text-sm break-words text-content-secondary">
                {message.text}
              </span>
              <RelativeTime iso={message.sent_at} className="shrink-0 text-xs text-content-tertiary" />
            </li>
          ))}
        </ul>
      </CardDisclosure>
    </CardShell>
  );
}
