import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

/** The group reads its running/failed counts through the vendored state hook.
 * Only `ToolCallGroup` touches it, so stubbing the one export leaves every
 * other renderer in this file on the real module. */
let groupParts: readonly unknown[] = [];
let groupIsLast = true;
vi.mock("@assistant-ui/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@assistant-ui/react")>()),
  useAuiState: (selector: (state: unknown) => unknown) =>
    selector({ message: { parts: groupParts, isLast: groupIsLast } }),
}));

import { NestLevel } from "@/components/grove/workspace/nesting";
import {
  ToolCallDetail,
  ToolCallGroup,
  ToolCallPart,
  ToolInvocationMeta,
} from "@/components/grove/workspace/tool-call-part";
import type { ToolCallView } from "@/lib/grove/adapters";

const BASH: ToolCallView = {
  name: "Bash",
  tool_use_id: "toolu_01AAA",
  status: "ok",
  input: { command: "rg -n 'todo' src", description: "Search the tree" },
  result: "src/a.ts:12: todo\n",
  duration_ms: 1450,
  body: "inline",
};

/** The vendored part props, with the fields this renderer never reads stubbed.
 * Written once so a test says what it is ABOUT rather than what it had to
 * construct. */
function header(call: ToolCallView | null, argsText = "rg -n todo"): string {
  return renderToStaticMarkup(
    <ToolCallPart
      type="tool-call"
      toolCallId={call?.tool_use_id ?? "positional-0"}
      toolName={call?.name ?? "Bash"}
      args={{}}
      argsText={argsText}
      {...(call ? { artifact: call } : {})}
      status={{ type: "complete" }}
      addResult={() => undefined}
      resume={() => undefined}
      respondToApproval={() => undefined}
    />,
  );
}

const body = (call: ToolCallView): string => renderToStaticMarkup(<ToolCallDetail call={call} />);

describe("a tool call's header", () => {
  it("uses a cataloged action and command instead of Used tool", () => {
    const markup = header(BASH);
    expect(markup).toContain("Ran");
    expect(markup).toContain("rg -n &#x27;todo&#x27; src");
    expect(markup).not.toContain("Used tool");
  });
  it("spins instead of ticking while the call is in flight", () => {
    const markup = header({ ...BASH, status: "running", result: null, duration_ms: null });
    expect(markup).toContain("animate-spin");
    expect(markup).toContain("Running");
    // The check is the settled mark and must not be drawn beside a spinner.
    expect(markup).toContain('data-tool-status="running"');
  });

  it("shows the duration once the call has settled, and no state word", () => {
    const markup = header(BASH);
    expect(markup).toContain("1.4s");
    expect(markup).not.toContain("animate-spin");
    expect(markup).not.toContain("Running");
  });

  it("says FAILED in words before it says it in colour", () => {
    const markup = header({ ...BASH, status: "error", result: "command not found" });
    expect(markup).toContain("Failed");
    expect(markup).toContain("text-destructive");
  });

  it("keeps the target readable and recoverable rather than bleeding the column", () => {
    const path = "src/very/long/path/that/would/overflow.py";
    const markup = header({ ...BASH, name: "Read", input: { file_path: path } });
    expect(markup).toContain("truncate");
    expect(markup).toContain(`title="${path}"`);
  });

  it("marks the call with its own correlation key, so parallel calls are distinguishable", () => {
    expect(header(BASH)).toContain('data-tool-use-id="toolu_01AAA"');
  });

  it("claims nothing about a provider that reports no detail", () => {
    const markup = header(null);
    expect(markup).toContain('data-tool-status="unknown"');
    expect(markup).not.toContain("data-tool-use-id");
  });
});

describe("a tool call's own nesting depth", () => {
  /** The trigger is always in the SSR markup (unlike the collapsible body,
   * which a closed `Collapsible` never renders — see `ToolCallDetail`'s own
   * docstring), so it is the one place this contract is observable without
   * forcing the collapsible open. Scoped to the trigger's own opening tag so
   * an unrelated `text-xs` elsewhere on the row (the duration, the target
   * line) cannot make the assertion pass for the wrong reason. */
  function triggerTag(markup: string): string {
    const idx = markup.indexOf('data-slot="tool-timeline-step"');
    const start = markup.lastIndexOf("<", idx);
    const end = markup.indexOf(">", idx);
    return markup.slice(start, end + 1);
  }

  it("keeps the vendored text-sm when rendered standalone, one step below the transcript's own text-base", () => {
    const tag = triggerTag(header(BASH));
    expect(tag).toContain("text-sm");
    expect(tag).not.toContain("text-xs");
  });

  it("keeps timeline actions at one reading size inside a group", () => {
    const markup = renderToStaticMarkup(
      <NestLevel>
        <ToolCallPart
          type="tool-call"
          toolCallId={BASH.tool_use_id}
          toolName={BASH.name}
          args={{}}
          argsText="rg -n todo"
          artifact={BASH}
          status={{ type: "complete" }}
          addResult={() => undefined}
          resume={() => undefined}
          respondToApproval={() => undefined}
        />
      </NestLevel>,
    );
    const tag = triggerTag(markup);
    expect(tag).toContain("text-sm");
    expect(tag).not.toContain("text-xs");
  });
});

describe("a tool call's body", () => {
  it("shows the request as named fields, with the command's own lines intact", () => {
    const markup = body(BASH);
    expect(markup).toContain("Request");
    expect(markup).toContain("command");
    expect(markup).toContain("rg -n &#x27;todo&#x27; src");
    expect(markup).toContain("Search the tree");
  });

  it("shows the response", () => {
    expect(body(BASH)).toContain("src/a.ts:12: todo");
  });

  it("distinguishes STILL RUNNING from RETURNED NOTHING — both have a null result", () => {
    const running = body({ ...BASH, status: "running", result: null, duration_ms: null });
    const empty = body({ ...BASH, result: null });
    expect(running).toContain("Still running");
    expect(running).not.toContain("Returned nothing");
    expect(empty).toContain("Returned nothing");
    expect(empty).not.toContain("Still running");
  });

  it("says so when there were no arguments, rather than showing an empty well", () => {
    expect(body({ ...BASH, input: null })).toContain("No arguments recorded");
  });

  it("renders a large body whole, with no height bound and no nested scroller", () => {
    // A tool response is one artifact read end to end, not a list to scan, and
    // it only renders inside a disclosure the reader already opened. A porthole
    // here hides content the same way the daemon's old character cap did — and
    // the daemon no longer caps, so the browser must not re-introduce the clip.
    const markup = body({ ...BASH, result: "x\n".repeat(500) });
    expect(markup).toContain("x\nx\n");
    expect(markup).not.toContain("max-h-64");
    expect(markup).not.toMatch(/class="[^"]*(?:^|\s)h-64\b/);
    expect(markup).not.toContain("Response was capped");
  });

  it("breaks a single unbroken line instead of letting it widen the transcript", () => {
    expect(body({ ...BASH, result: "a".repeat(4000) })).toContain("wrap-break-word");
  });

  it("prints the correlation key", () => {
    expect(body(BASH)).toContain("toolu_01AAA");
  });
});

describe("the invocation line on a card that owns its own disclosure", () => {
  it("renders nothing at all when the provider reported no detail", () => {
    expect(renderToStaticMarkup(<ToolInvocationMeta tool={null} />)).toBe("");
  });

  it("spins while the edit's own call is still in flight", () => {
    const markup = renderToStaticMarkup(
      <ToolInvocationMeta tool={{ ...BASH, status: "running", result: null, duration_ms: null }} />,
    );
    expect(markup).toContain("animate-spin");
    expect(markup).toContain("Running");
  });

  it("reports the duration for a settled one, and never a cap", () => {
    const markup = renderToStaticMarkup(<ToolInvocationMeta tool={BASH} />);
    expect(markup).toContain("1.4s");
    expect(markup).not.toContain("capped");
  });
});

/** A group's mount state comes from the runtime's message boundary. The live
 * browser test owns the transition and manual-toggle contracts. */
describe("a group of tool calls", () => {
  function group(running: number): string {
    groupIsLast = true;
    groupParts = [
      { type: "tool-call", toolName: "Bash", argsText: "", artifact: { ...BASH, status: running > 0 ? "running" : "ok" } },
    ];
    return renderToStaticMarkup(
      <ToolCallGroup group={{ indices: [0] } as never}>
        <span>call body</span>
      </ToolCallGroup>,
    );
  }

  it("stays folded up once every call in it has settled", () => {
    const markup = group(0);
    expect(markup).toContain('data-state="closed"');
    expect(markup).toContain("1 step · 1 command");
    expect(markup).toContain('data-testid="tool-timeline-icons"');
  });

  it("opens itself while a call is in flight, so a spinner is never one click deep", () => {
    const markup = group(1);
    expect(markup).toContain('data-state="open"');
    expect(markup).toContain("1 running");
  });

  it("does not open a settled historical group just because a later message follows", () => {
    groupIsLast = false;
    groupParts = [
      { type: "tool-call", toolName: "Bash", argsText: "", artifact: { ...BASH, status: "ok" } },
    ];
    const markup = renderToStaticMarkup(
      <ToolCallGroup group={{ indices: [0] } as never}>
        <span>call body</span>
      </ToolCallGroup>,
    );
    expect(markup).toContain('data-state="closed"');
  });
});
