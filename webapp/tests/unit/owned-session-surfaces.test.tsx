import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { OwnedSessionBadge } from "@/components/grove/fleet/badges";
import { SendKeysCard } from "@/components/grove/workspace/send-keys";

/**
 * A Grove-owned session is marked on the fleet card and has no terminal to
 * type into. Both facts are one wire flag (`WorkspaceStateView.native`), and
 * each surface reads it directly rather than inferring it from the agent name.
 */
describe("OwnedSessionBadge", () => {
  it("marks only the owned case — the terminal gets no second mark", () => {
    expect(renderToStaticMarkup(<OwnedSessionBadge native />)).toContain(
      'data-testid="owned-session-badge"',
    );
    expect(renderToStaticMarkup(<OwnedSessionBadge native={false} />)).toBe("");
  });
});

function keys(props: { native: boolean; canInterrupt: boolean }): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <SendKeysCard workspaceId="w1" status="active" canInterrupt={props.canInterrupt} native={props.native} />
    </QueryClientProvider>,
  );
}

describe("SendKeysCard on a native session", () => {
  it("withholds every key group and keeps the provider-channel cancel", () => {
    const html = keys({ native: true, canInterrupt: true });
    expect(html).toContain('data-native="true"');
    expect(html).toContain('data-testid="chat-interrupt"');
    expect(html).not.toContain('data-testid="send-keys-arrows"');
    expect(html).not.toContain('data-testid="send-keys-commands"');
  });

  it("renders nothing at all when there is neither a terminal nor a turn to cancel", () => {
    expect(keys({ native: true, canInterrupt: false })).toBe("");
  });

  it("keeps the full keyboard for a terminal session", () => {
    const html = keys({ native: false, canInterrupt: false });
    expect(html).toContain('data-testid="send-keys-arrows"');
    expect(html).not.toContain('data-native="true"');
  });
});
