import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { NestLevel } from "@/components/grove/workspace/nesting";
import {
  ToolCallDetail,
  ToolCallPart,
  ToolInvocationMeta,
} from "@/components/grove/workspace/tool-call-part";
import type { ToolCallView } from "@/lib/grove/adapters";

const BASH: ToolCallView = {
  name: "Bash",
  tool_use_id: "toolu_01AAA",
  status: "ok",
  input: { command: "rg -n 'todo' src", description: "Search the tree" },
  input_truncated: false,
  result: "src/a.ts:12: todo\n",
  result_truncated: false,
  duration_ms: 1450,
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
    const markup = header(BASH, "src/very/long/path/that/would/overflow.py");
    expect(markup).toContain("truncate");
    expect(markup).toContain('title="src/very/long/path/that/would/overflow.py"');
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
    const idx = markup.indexOf('data-slot="tool-fallback-trigger"');
    const start = markup.lastIndexOf("<", idx);
    const end = markup.indexOf(">", idx);
    return markup.slice(start, end + 1);
  }

  it("keeps the vendored text-sm when rendered standalone, one step below the transcript's own text-base", () => {
    const tag = triggerTag(header(BASH));
    expect(tag).toContain("text-sm");
    expect(tag).not.toContain("text-xs");
  });

  it("steps down to the group's own floor once nested — the inversion this file exists to fix", () => {
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
    expect(tag).toContain("text-xs");
    expect(tag).not.toContain("text-sm");
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

  it("STATES truncation — an ellipsis inside real output would say nothing", () => {
    const markup = body({ ...BASH, input_truncated: true, result_truncated: true });
    expect(markup).toContain("Arguments were capped");
    expect(markup).toContain("Response was capped");
    expect(body(BASH)).not.toContain("capped");
  });

  it("bounds a large body with max-h and scrolls it internally — never a fixed h-*", () => {
    // The contract is "this well bounds its own height and scrolls", which is
    // what `max-h-* overflow-y-auto` says. `h-*` is the idiom that shipped a
    // silent clip once already.
    const markup = body({ ...BASH, result: "x\n".repeat(500) });
    expect(markup).toContain("max-h-64");
    expect(markup).toContain("overflow-y-auto");
    // `\bh-64\b` would match INSIDE `max-h-64` — a word boundary sits between
    // the hyphen and the `h`, so the guard against the wrong idiom fires on the
    // right one. Anchor on a class-list boundary instead.
    expect(markup).not.toMatch(/class="[^"]*(?:^|\s)h-64\b/);
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

  it("reports the duration and the cap for a settled one", () => {
    const markup = renderToStaticMarkup(
      <ToolInvocationMeta tool={{ ...BASH, result_truncated: true }} />,
    );
    expect(markup).toContain("1.4s");
    expect(markup).toContain("capped");
  });
});
