import { expect, it } from "vitest";
import { messagesFromTurns, GROVE_DATA_PART } from "@/lib/grove/adapters/transcript";
import { agentLabel } from "@/lib/grove/adapters/agent-message";
import type { SessionTurnView } from "@/lib/grove/api";

const turnWith = (mailbox: NonNullable<SessionTurnView["entries"][number]["mailbox"]>): SessionTurnView => ({
  user_text: "Human prompt",
  started_at: null,
  entries: [{ role: "notification", text: "Short cooked summary", mailbox }],
});

/**
 * A delivery now rides the TOOL RUN rather than being its own data message.
 *
 * Flushing the run to emit a loose card split one continuous sequence of calls
 * into two collapsible groups with the card wedged between them. The envelope
 * travels on the part's own `groveMailbox` key, which `ToolCallPart` reads to
 * draw the card in place — so the assertions below read that key rather than a
 * `data-mailbox` part, and the grouping test underneath is what stops the
 * split returning.
 */
const mailboxEnvelopes = (turn: SessionTurnView) =>
  messagesFromTurns([turn])
    .flatMap((m) => (Array.isArray(m.content) ? m.content : []))
    .flatMap((p) => {
      const carried = (p as { groveMailbox?: { body: string; handoff: boolean } }).groveMailbox;
      return carried ? [carried] : [];
    });

it("preserves mailbox identity and body independently of the cooked notification summary", () => {
  const [envelope] = mailboxEnvelopes(
    turnWith({
      sender: "reviewer",
      recipient: null,
      subject: "Findings",
      body: "Full\n\n**Markdown** body",
      kind: "notice",
    }),
  );
  expect(envelope).toEqual({
    from: "reviewer",
    to: "This session",
    subject: "Findings",
    body: "Full\n\n**Markdown** body",
    handoff: false,
  });
});

it("keeps a delivery inside ONE tool group instead of splitting the run", () => {
  // Two calls with a delivery between them: the defect rendered this as
  // group / card / group, so the count of assistant messages is the assertion.
  const turn: SessionTurnView = {
    user_text: "Human prompt",
    started_at: null,
    entries: [
      { role: "tool", text: "Bash ls", tool: null },
      {
        role: "notification",
        text: "Peer message",
        mailbox: {
          sender: "a".repeat(32),
          recipient: "b".repeat(32),
          subject: "request",
          body: "Please review PR #42",
          kind: "peer",
        },
      },
      { role: "tool", text: "Read file.ts", tool: null },
    ],
  } as SessionTurnView;

  const assistant = messagesFromTurns([turn]).filter((m) => m.role === "assistant");
  expect(assistant).toHaveLength(1);
  expect(Array.isArray(assistant[0].content) ? assistant[0].content : []).toHaveLength(3);
  // And no loose data-mailbox message survives beside the group.
  const loose = messagesFromTurns([turn])
    .flatMap((m) => (Array.isArray(m.content) ? m.content : []))
    .filter((p) => p.type === GROVE_DATA_PART.mailbox);
  expect(loose).toHaveLength(0);
});

/**
 * The handoff flag comes from the wire's `kind` and from nothing else.
 *
 * Both fixtures below have a populated sender and recipient, so anything that
 * inferred "this is a handoff" from the fields being filled would mark them
 * identically — which is exactly the inference the engine's `kind` exists to
 * replace.
 */
it.each([
  { kind: "peer" as const, handoff: true },
  { kind: "notice" as const, handoff: false },
])("marks a $kind envelope handoff=$handoff", ({ kind, handoff }) => {
  const [envelope] = mailboxEnvelopes(
    turnWith({
      sender: "a".repeat(32),
      recipient: "b".repeat(32),
      subject: "request",
      body: "Please review PR #42",
      kind,
    }),
  );
  expect(envelope).toBeDefined();
  expect(envelope.handoff).toBe(handoff);
});

it("shortens a bare workspace id but leaves every other label alone", () => {
  expect(agentLabel("f1ded3a3ab894cf6a3e6a1172b7a891c")).toBe("f1ded3a3ab");
  // A slot-qualified address and a human display name are not ids; truncating
  // either would be inventing a different name.
  expect(agentLabel("f1ded3a3ab894cf6a3e6a1172b7a891c/reviewer")).toBe(
    "f1ded3a3ab894cf6a3e6a1172b7a891c/reviewer",
  );
  expect(agentLabel("reviewer")).toBe("reviewer");
});
