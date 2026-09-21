import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const route = readFileSync(
  new URL("../../app/api/grove/[...path]/route.ts", import.meta.url),
  "utf8",
);

describe("fleet stream BFF forwarding", () => {
  it("recognizes the root-scoped fleet stream as SSE rather than JSON", () => {
    // The browser opens this EventSource against the cookie-authenticated BFF.
    // If the catch-all route does not classify it as a stream, `proxy()` consumes
    // its body as text and the child-state view freezes after the first frame.
    expect(route).toContain('path.length === 4 && path[0] === "workspaces" && path[2] === "fleet" && path[3] === "stream"');
    expect(route).toContain("proxyStream(request, path)");
  });
});
