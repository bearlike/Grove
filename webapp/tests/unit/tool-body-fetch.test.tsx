import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ToolCallDetail } from "@/components/grove/workspace/tool-call-part";
import { ToolBodyProvider } from "@/lib/grove/hooks";
import { shouldFetchToolBody, type ToolCallView } from "@/lib/grove/adapters";

/**
 * WHEN a withheld tool body is fetched — and, far more importantly, when it is
 * not.
 *
 * The daemon's windowed `/turns` read drops a settled call's request/result
 * outside the tail turn and marks it `body: "available"`. The saving is only
 * real if the client then asks for exactly the bodies a reader opens; a request
 * per rendered step would be strictly worse than the payload it replaced.
 *
 * The gate itself is asserted through the PURE predicate rather than by
 * counting requests in this environment. `renderToStaticMarkup` runs no
 * effects, so react-query never reaches a transport here at all — a request
 * count would read zero whether the gate worked or the component had simply
 * never mounted, which is the vacuous-guard shape. The rendered half below
 * pins what a reader SEES while a body is outstanding; the real request count
 * is a browser assertion.
 */

const SETTLED: ToolCallView = {
  name: "Bash",
  tool_use_id: "toolu_withheld",
  status: "ok",
  input: null,
  result: null,
  duration_ms: 1450,
  body: "available",
};

function render(call: ToolCallView, open: boolean): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <ToolBodyProvider source={{ kind: "workspace", workspaceId: "w1", sessionId: "s1" }}>
        <ToolCallDetail call={call} open={open} />
      </ToolBodyProvider>
    </QueryClientProvider>,
  );
}

describe("the withheld-body fetch gate", () => {
  it("does not fetch a collapsed body", () => {
    expect(shouldFetchToolBody("available", false)).toBe(false);
  });

  it("fetches an opened body that the daemon withheld", () => {
    expect(shouldFetchToolBody("available", true)).toBe(true);
  });

  it("never fetches an INLINE body — it is already in the payload", () => {
    expect(shouldFetchToolBody("inline", true)).toBe(false);
    expect(shouldFetchToolBody("inline", false)).toBe(false);
  });

  it("never fetches a call that HAS no body", () => {
    // A fetch here could only ever come back empty, so the affordance would be
    // a lie about what is one click away.
    expect(shouldFetchToolBody("none", true)).toBe(false);
    expect(shouldFetchToolBody("none", false)).toBe(false);
  });
});

describe("what a reader sees while a withheld body is outstanding", () => {
  it("says the body is loading rather than claiming the call returned nothing", () => {
    // The two are different facts and the whole point of `body` is that a
    // client can tell them apart — reporting "Returned nothing" for a withheld
    // body is the silent-cap failure wearing new words.
    const markup = render(SETTLED, true);
    expect(markup).toContain("Loading…");
    expect(markup).not.toContain("Returned nothing.");
  });

  it("reports a genuinely empty call as having returned nothing, with no loader", () => {
    const markup = render({ ...SETTLED, body: "none" }, true);
    expect(markup).toContain("Returned nothing.");
    expect(markup).not.toContain("Loading…");
  });

  it("renders an inline body whole, through the same body component", () => {
    const markup = render(
      { ...SETTLED, body: "inline", input: { command: "pytest -q" }, result: "2 passed" },
      true,
    );
    expect(markup).toContain("2 passed");
    expect(markup).toContain("pytest -q");
    expect(markup).not.toContain("Loading…");
  });
});
