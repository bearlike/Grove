"use client";

import { lazy, Suspense, useState } from "react";
import { TextMessagePartProvider } from "@assistant-ui/react";

import { AppIcon } from "@/components/grove/app-icon";
import { toolPresentation } from "@/lib/grove/adapters/tool-catalog";

const MAILBOX_ICON = toolPresentation("Mailbox").icon;

const MarkdownText = lazy(() => import("@/components/assistant-ui/markdown-text").then((module) => ({ default: module.MarkdownText })));
import { AgentHandoff } from "@/components/elements/agent-handoff";
import { CardDisclosure, CardShell } from "@/components/grove/card";
import { agentLabel, type AgentMessageData } from "@/lib/grove/adapters/agent-message";
import { NewTabLinks } from "./new-tab-links";

/** Mail has its own identity and readable body, not a tiny log-line treatment. */
export function AgentMessage({ message, children }: { message: AgentMessageData; children?: React.ReactNode }): React.ReactNode {
  const [open, setOpen] = useState(false);
  return (
    <CardShell data-testid="agent-message" data-collapsed={!open}>
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        header
        summary={
          <>
            <AppIcon
              slug={MAILBOX_ICON}
              className="size-4 shrink-0"
              data-testid="mailbox-card-icon"
            />
            {message.handoff ? <HandoffSummary message={message} /> : <NoticeSummary message={message} />}
          </>
        }
        contentClassName="p-3"
      >
        <AgentMessageBody text={message.body} />
        {children}
      </CardDisclosure>
    </CardShell>
  );
}

/**
 * A real peer delivery, drawn with the vendored handoff element.
 *
 * Composed rather than restyled: the two agent pills, the arrow between them
 * and the reason line are exactly this message's shape, so the element renders
 * it with no colour, radius or spacing decided here.
 *
 * **`settled` is FALSE here, and that is a contrast decision rather than a
 * claim about delivery.** Passing `true` compounds the element's own
 * `text-foreground/45` on the sender pill with a further `opacity-45`, which
 * measured **1.78:1** — the pill naming who sent you the message being the one
 * thing on the card a reader cannot read. The cost of `false` is that a Grove
 * handoff never draws the in-transit treatment, which is honest: Grove has no
 * "delivery in progress" state to show — `POST` answers and the transcript
 * records what arrived.
 *
 * **`settled={false}` alone was NOT enough, and the reason is the SURFACE the
 * pill sits on.** The vendor's remaining 45% dim was measured at 4.37:1 against
 * the page background — but this card is `surface-raised`, `oklch(0.213)` in
 * dark against the page's `oklch(0.155)`, so the real backdrop is lighter and
 * the real ratio lower. A reader reported the pill as unreadable with the
 * `settled` fix already shipped, which is the measurement that counts.
 * `handoff-agent` (in `globals.css`) restores the pill to the same tier its
 * recipient uses. **A contrast number is only true for the background it was
 * measured against — measure on the surface the element actually lands on,
 * not the page.**
 *
 * The vendored file stays untouched, per the compose-never-restyle rule: the
 * override is a class at the composition point, which is the same seam the
 * `.attachment-card` and terminal-palette rules already use.
 *
 * `carried` is empty: the element lists context items carried across a handoff,
 * and Grove's envelope carries a body rather than an itemised context set.
 * Splitting the body into bullets would be Grove summarising a peer's words.
 */
function HandoffSummary({ message }: { message: AgentMessageData }): React.ReactNode {
  return (
    <span className="flex min-w-0 flex-1 text-left" data-testid="agent-handoff-summary">
      <AgentHandoff
        from={message.from ? agentLabel(message.from) : "Sender not recorded"}
        to={message.to ? agentLabel(message.to) : "Recipient not recorded"}
        reason={handoffReason(message)}
        carried={[]}
        settled={false}
        className="handoff-agent max-w-none"
      />
    </span>
  );
}

/**
 * What the handoff line says — Grove's own words about the DELIVERY.
 *
 * Never the peer's body, even truncated: the envelope carries no subject, so
 * `subject` here is the delivery intent, and paraphrasing a stranger's message
 * would put their words in Grove's voice. The body is one click away.
 */
function handoffReason(message: AgentMessageData): string {
  if (message.state) return message.state;
  return message.subject === "request"
    ? "Asked this session to do something."
    : "Sent this session a message.";
}

/**
 * A harness notice — the SAME vocabulary as a handoff, one tier quieter.
 *
 * This used to be a hand-composed row: a mail glyph, two raw ids either side of
 * an arrow, and a subject line under it. Beside the handoff card's two pills it
 * read as a different feature, and on one transcript the reader saw both within
 * a few rows of each other — which is the mixed-design report of 2026-09-15.
 *
 * It is the same shape of fact (something addressed this session), so it takes
 * the same element. What separates the two is the `notice` mark and the absent
 * `handoff-agent` override: a notice keeps the vendored dim on its sender,
 * because a task id is not an agent whose name a reader has to be able to read.
 * A peer's workspace id IS, which is what that override exists for.
 *
 * `agentLabel` applies here too — a task notification's sender is routinely a
 * 32-char id (`a347d180c04f7574f…` in the report), and it was printed whole.
 */
function NoticeSummary({ message }: { message: AgentMessageData }): React.ReactNode {
  return (
    <span className="flex min-w-0 flex-1 text-left" data-testid="agent-notice-summary">
      <AgentHandoff
        from={message.from ? agentLabel(message.from) : "Sender not recorded"}
        to={message.to ? agentLabel(message.to) : "This session"}
        reason={message.subject || message.state || "Agent message"}
        carried={[]}
        settled={false}
        // `handoff-agent` too: the vendored 45% dim measured 4.33:1 on this
        // card, just under the 4.5:1 floor. The tier difference a notice keeps
        // is its REASON line and the absent emphasis on its recipient, not an
        // unreadable sender — quieter must not mean below the floor.
        className="handoff-agent max-w-none"
      />
    </span>
  );
}

/** Native text-part scope makes the unmodified markdown renderer accept data-part prose. */
export function AgentMessageBody({ text }: { text: string }): React.ReactNode {
  return (
    // Response content, so it takes the same link rule the transcript's own
    // answers do — a mailbox body is prose somebody else wrote and its links
    // are citations, not navigation within this page.
    <NewTabLinks
      className="min-w-0 break-words text-base text-content-primary"
      data-testid="agent-message-body"
    >
      <TextMessagePartProvider text={text}>
        <Suspense fallback={<p className="whitespace-pre-wrap">{text}</p>}>
          <MarkdownText />
        </Suspense>
      </TextMessagePartProvider>
    </NewTabLinks>
  );
}
