import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ push: () => undefined }),
  useSearchParams: () => new URLSearchParams(),
}));

// The event stream is the I/O edge: an EventSource has no place in a static
// render, and the project pill only needs "no snapshot yet".
vi.mock("@/lib/grove/hooks/stream", async (original) => ({
  ...(await original<typeof import("@/lib/grove/hooks/stream")>()),
  useActivityStream: () => ({
    snapshot: null,
    error: null,
    isPending: true,
    attempt: 0,
    connected: false,
    refetch: () => undefined,
  }),
}));

import { LaunchPillGroup } from "@/components/grove/launch/control-pill";
import { LaunchComposer } from "@/components/grove/launch/launch-composer";
import { LaunchDraftProvider } from "@/components/grove/launch/launch-draft";
import { groveKeys } from "@/lib/grove/hooks/keys";

/**
 * The composer as BOTH surfaces mount it: inside the one shell-level draft,
 * with no props carrying state in. If the composer still needed the surface to
 * hand it `launch`/`dictation`, More options could not mount it at all.
 */
function render(): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(groveKeys.whoami, { speech: false });
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <LaunchDraftProvider>
        <LaunchPillGroup>
          <LaunchComposer disabled={false} expanded={false} inputRef={{ current: null }} expandControl={null} />
        </LaunchPillGroup>
      </LaunchDraftProvider>
    </QueryClientProvider>,
  );
}

describe("the composer inside the shared draft", () => {
  const html = render();

  it("renders the vendored anatomy from the draft alone", () => {
    expect(html).toContain('data-testid="launch-composer"');
    expect(html).toContain('data-testid="launch-input"');
    expect(html).toContain('data-slot="composer-toolbar"');
  });

  it("holds Send while no project has answered, rather than arming it", () => {
    // An empty draft has nothing to send and no defaults to send it with.
    expect(html).toMatch(/aria-label="Send message"[^>]*disabled=""|disabled=""[^>]*aria-label="Send message"/);
  });

  it("names the model it will use as Agent default, never a catalog guess", () => {
    expect(html).toContain("Agent default");
  });
});
