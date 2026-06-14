import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPanel } from "@/components/chat/chat-panel";
import type { SessionDetailView, SessionSummaryView } from "@/lib/grove/types";

const SESSION: SessionSummaryView = {
  session_id: "s1",
  adapter_kind: "claude_code",
  provenance: "grove_launched",
  workspace_id: "w1",
  workspace_title: "feat depth",
  workspace_branch: "feat/depth",
  git_branch: "feat/depth",
  created_at: "2026-06-10T10:00:00Z",
  modified_at: "2026-06-10T11:00:00Z",
  size_bytes: 4096,
  title: "wire the panel",
  first_prompt: "build the panel",
  last_prompt: "ship it",
  activity: {
    state: "idle",
    title: null,
    current_task: null,
    human_turns: 1,
    assistant_replies: 1,
    replies_per_turn: [1],
    tool_calls: 0,
    active_subagents: 0,
    model: "claude-opus-4-8",
    tokens_in: 100,
    tokens_out: 10,
    last_event_at: null,
    needs_attention: false,
    error_detail: null,
  },
};

const DETAIL: SessionDetailView = {
  session: SESSION,
  turns: [
    {
      user_text: "ship it",
      started_at: "2026-06-10T10:00:00Z",
      entries: [
        { role: "assistant", text: "Shipping the panel now." },
        {
          role: "notification",
          text: "Background task completed: Explore\nThe full subagent result body.",
        },
      ],
    },
  ],
};

/** Route the panel's two reads by URL — sessions list, then the turns digest. */
function stubFetch() {
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: RequestInfo | URL) => {
      const u = String(url);
      if (u.includes("/turns")) return json(DETAIL);
      if (u.includes("/sessions")) return json([SESSION]);
      throw new Error(`unexpected fetch ${u}`);
    }),
  );
}

function r(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>);
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatPanel", () => {
  it("tags each bubble with an IRC-style speaker label: you (clay) / agent (blue)", async () => {
    stubFetch();
    r(<ChatPanel workspaceId="w1" />);

    const messages = await screen.findAllByTestId("chat-message");
    const user = messages.find((m) => m.dataset.role === "user");
    const assistant = messages.find((m) => m.dataset.role === "assistant");
    // data-role continuity: both bubbles still carry the wire role.
    expect(user).toBeDefined();
    expect(assistant).toBeDefined();

    const userLabel = user!.querySelector('[data-testid="role-label"]')!;
    const agentLabel = assistant!.querySelector('[data-testid="role-label"]')!;
    expect(userLabel.textContent).toBe("you");
    expect(agentLabel.textContent).toBe("agent");

    // Same tokens as TurnsView and the TUI (class-based — jsdom can't
    // resolve `var()`/theme utilities, so the class is the seam).
    expect(userLabel.className).toContain("text-primary");
    expect(agentLabel.className).toContain("text-[var(--ref-info)]");
    // The user label rides the right-aligned bubble; the agent label leads.
    expect(userLabel.className).toContain("self-end");
    expect(agentLabel.className).toContain("self-start");
  });

  it("renders a notification as a quiet row: summary visible, result behind a disclosure", async () => {
    stubFetch();
    r(<ChatPanel workspaceId="w1" />);

    const note = await screen.findByTestId("chat-notification");
    expect(note).toHaveTextContent("Background task completed: Explore");
    // Not a bubble, not a plain note — its own seam.
    expect(note.querySelector('[data-testid="chat-message"]')).toBeNull();
    expect(note).not.toHaveTextContent("The full subagent result body.");

    fireEvent.click(note.querySelector("button")!);
    expect(note).toHaveTextContent("The full subagent result body.");
  });
});
