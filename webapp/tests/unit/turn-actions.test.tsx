import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  type FeedbackAdapter,
} from "@assistant-ui/react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TurnActions } from "@/components/grove/workspace/turn-actions";
import type { DigestEntryView, SessionTurnView } from "@/lib/grove/api";
import { useTranscriptRuntime } from "@/lib/grove/runtime";

const TURN: SessionTurnView = {
  user_text: "go",
  started_at: "2026-06-09T08:00:00Z",
  entries: [
    { role: "assistant", text: "looking" },
    { role: "status", text: "idle" },
    { role: "assistant", text: "done" },
  ] as DigestEntryView[],
};

const ADAPTER: FeedbackAdapter = { submit: () => {} };

function Harness({ feedback }: { feedback: boolean }) {
  const { runtime } = useTranscriptRuntime({
    turns: [TURN],
    ...(feedback ? { feedback: { adapter: ADAPTER, stamp: (m) => m } } : {}),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {/* The thread port mounts exactly this under every message; the full
          port is not rendered because its markdown pulls CSS vitest cannot load. */}
      <ThreadPrimitive.Messages>
        {() => (
          <MessagePrimitive.Root>
            <TurnActions />
          </MessagePrimitive.Root>
        )}
      </ThreadPrimitive.Messages>
    </AssistantRuntimeProvider>
  );
}

const count = (markup: string, needle: string): number => markup.split(needle).length - 1;

describe("the per-turn action bar (thread delta 17)", () => {
  it("renders ONE bar per turn, never Reload, with thumbs when feedback is wired", () => {
    const markup = renderToStaticMarkup(<Harness feedback />);
    // Two assistant texts and a note in one turn: one bar, under the answer.
    expect(count(markup, 'data-slot="aui_assistant-message-footer"')).toBe(1);
    expect(markup).toContain("lucide-thumbs-up");
    expect(markup).toContain("lucide-thumbs-down");
    expect(markup).not.toContain("lucide-refresh-cw");
  });

  it("is Copy alone where no feedback adapter exists", () => {
    const markup = renderToStaticMarkup(<Harness feedback={false} />);
    expect(count(markup, 'data-slot="aui_assistant-message-footer"')).toBe(1);
    expect(markup).toContain("lucide-copy");
    expect(markup).not.toContain("lucide-thumbs-up");
  });
});

describe("the follow-up asks why, in the verdict's own words", () => {
  it("a thumbs-up asks what went well and a thumbs-down what went wrong", async () => {
    const { FeedbackDialog } = await import("@/components/grove/workspace/feedback-dialog");
    const render = (tone: "positive" | "negative") =>
      renderToStaticMarkup(
        <FeedbackDialog tone={tone} reasons={["A"]} selected={[]} note="" sent={false} />,
      );
    const up = render("positive");
    const down = render("negative");
    expect(up).toContain("What went well?");
    expect(up).toContain("lucide-thumbs-up");
    expect(up).not.toContain("lucide-thumbs-down");
    expect(down).toContain("What went wrong?");
    expect(down).toContain("lucide-thumbs-down");
  });
});
