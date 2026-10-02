import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TurnActions } from "@/components/grove/workspace/turn-actions";
import {
  LiveTurnContext,
  TurnFilesCard,
  turnFileStats,
} from "@/components/grove/workspace/turn-files";
import { GROVE_TURN_ANSWER, GROVE_TURN_FILES, messagesFromTurns } from "@/lib/grove/adapters";
import type { DigestEntryView, SessionTurnView } from "@/lib/grove/api";
import { useTranscriptRuntime } from "@/lib/grove/runtime";

const STARTED = "2026-09-30T08:00:00Z";

function edit(displayPath: string, oldText: string, newText: string): DigestEntryView {
  return {
    role: "file_edit",
    text: `Edit ${displayPath}`,
    file_edit: { path: `/repo/${displayPath}`, display_path: displayPath, old_text: oldText, new_text: newText },
  } as DigestEntryView;
}

const TURN: SessionTurnView = {
  user_text: "go",
  started_at: STARTED,
  entries: [
    { role: "assistant", text: "editing" },
    edit("docs/index.md", "a\nb\n", "a\nc\nd\n"),
    edit("README.md", "", "one\ntwo\n"),
    // The same file again: one row, both edits' tallies summed.
    edit("docs/index.md", "a\nc\nd\n", "a\nc\nd\ne\n"),
    { role: "assistant", text: "done" },
  ] as DigestEntryView[],
};

function Harness({ turns, live = null }: { turns: SessionTurnView[]; live?: string | null }) {
  const { runtime } = useTranscriptRuntime({ turns });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <LiveTurnContext.Provider value={live}>
        <ThreadPrimitive.Messages>
          {() => (
            <MessagePrimitive.Root>
              <TurnActions />
            </MessagePrimitive.Root>
          )}
        </ThreadPrimitive.Messages>
      </LiveTurnContext.Provider>
    </AssistantRuntimeProvider>
  );
}

const count = (html: string, needle: string) => html.split(needle).length - 1;

describe("the turn's file summary", () => {
  it("stamps the turn's edits on its ANSWER only, beside the answer marker", () => {
    const messages = messagesFromTurns([TURN]);
    const stamped = messages.filter((m) => m.metadata?.custom?.[GROVE_TURN_FILES]);
    expect(stamped).toHaveLength(1);
    expect(stamped[0]?.metadata?.custom?.[GROVE_TURN_ANSWER]).toBe(STARTED);
    expect(stamped[0]?.metadata?.custom?.[GROVE_TURN_FILES]).toHaveLength(3);
  });

  it("folds repeat edits of one path into one row, summing each edit's own diff", () => {
    const messages = messagesFromTurns([TURN]);
    const edits = messages.flatMap((m) => (m.metadata?.custom?.[GROVE_TURN_FILES] as never[]) ?? []);
    expect(turnFileStats(edits)).toEqual([
      // a,b → a,c,d is +2 −1; then +1 −0.
      { path: "/repo/docs/index.md", displayPath: "docs/index.md", additions: 3, deletions: 1 },
      { path: "/repo/README.md", displayPath: "README.md", additions: 2, deletions: 0 },
    ]);
  });

  it("renders once per completed turn, collapsed, above the action bar", () => {
    const html = renderToStaticMarkup(<Harness turns={[TURN, { ...TURN, started_at: "2026-09-30T09:00:00Z" }]} />);
    expect(count(html, 'data-testid="turn-files"')).toBe(2);
    expect(html).toContain("Files (2)");
    expect(html).toContain('data-collapsed="true"');
    expect(html.indexOf('data-testid="turn-files"')).toBeLessThan(html.indexOf("aui_assistant-message-footer"));
  });

  it("waits while its own turn is still live, and only that turn", () => {
    const later = { ...TURN, started_at: "2026-09-30T09:00:00Z" };
    const html = renderToStaticMarkup(<Harness turns={[TURN, later]} live={later.started_at} />);
    expect(count(html, 'data-testid="turn-files"')).toBe(1);
  });

  it("open, rules the header off from the list and leads each row with its file-type icon", () => {
    const files = [
      { path: "/repo/README.md", displayPath: "README.md", additions: 2, deletions: 0 },
      { path: "/repo/src/app.tsx", displayPath: "src/app.tsx", additions: 1, deletions: 1 },
    ];
    const open = renderToStaticMarkup(<TurnFilesCard files={files} open onOpenChange={() => {}} />);
    const toggle = open.indexOf("turn-files-toggle");
    const separator = open.indexOf("turn-files-separator");
    expect(toggle).toBeGreaterThan(-1);
    expect(separator).toBeGreaterThan(toggle);
    expect(separator).toBeLessThan(open.indexOf('data-testid="turn-file"'));
    // One icon per row, each ahead of the file name it labels.
    expect(count(open, 'data-testid="file-type-icon"')).toBe(2);
    const rows = open.split('data-testid="turn-file"').slice(1);
    expect(rows).toHaveLength(2);
    rows.forEach((row, i) => {
      const name = ["README.md", "app.tsx"][i]!;
      expect(row.indexOf("file-type-icon")).toBeGreaterThan(-1);
      expect(row.indexOf("file-type-icon")).toBeLessThan(row.indexOf(`>${name}<`));
    });
    // Collapsed draws neither the rule nor the rows.
    const shut = renderToStaticMarkup(<TurnFilesCard files={files} open={false} onOpenChange={() => {}} />);
    expect(shut).not.toContain("turn-files-separator");
    expect(shut).not.toContain("file-type-icon");
  });

  it("renders nothing for a turn that edited no files", () => {
    const quiet: SessionTurnView = {
      user_text: "go",
      started_at: STARTED,
      entries: [{ role: "assistant", text: "done" }] as DigestEntryView[],
    };
    expect(renderToStaticMarkup(<Harness turns={[quiet]} />)).not.toContain("turn-files");
  });
});
