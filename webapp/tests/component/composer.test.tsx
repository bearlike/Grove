import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Composer } from "@/components/composer/composer";
import { useUiStore } from "@/lib/grove/ui-store";
import type { AgentSummaryView, WorkspaceStateView } from "@/lib/grove/types";

// The composer is the hero create surface (#96 deliverable A). It reads/writes
// the Zustand composer slice and pulls its data hooks itself, so the test mocks
// the hooks module (the data + the create mutation) and `next/navigation` (the
// post-create route). We assert the PUBLIC contract — what request the prompt +
// pickers build, the model-pill visibility rule, the custom-id escape hatch, and
// the collapsed-by-default Advanced disclosure — never internals.

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

// One shared mutate spy the test inspects; `mutate` invokes onSuccess so the
// route + reset path runs exactly as production.
const mutate = vi.fn((_req: unknown, opts?: { onSuccess?: (ws: WorkspaceStateView) => void }) => {
  opts?.onSuccess?.({ id: "ws-new" } as WorkspaceStateView);
});

let agentList: AgentSummaryView[] = [
  { name: "claude", kind: "claude_code", description: "Anthropic" },
  { name: "mewbo", kind: "mewbo", description: "research" },
];

vi.mock("@/lib/grove/hooks", () => ({
  useActivityStream: () => ({
    snapshot: {
      projects: [{ repo_root: "/repos/Grove", repo_name: "Grove", workspaces: [] }],
    },
    connected: true,
    lastEventAt: null,
    error: null,
    refresh: vi.fn(),
  }),
  useAgents: () => ({ data: agentList }),
  useBranches: () => ({ data: [] }),
  useCreateWorkspace: () => ({ mutate, isPending: false, error: null }),
}));

function resetComposer(overrides: Record<string, unknown> = {}) {
  useUiStore.setState({
    composer: {
      prompt: "",
      agentName: null,
      model: null,
      repoRoot: null,
      branchMode: "auto",
      baseRef: "HEAD",
      newName: "",
      existingName: "",
      remoteRef: "",
      remoteLocal: "",
      skipInit: false,
      advancedOpen: false,
      ...overrides,
    },
  });
}

beforeEach(() => {
  push.mockClear();
  mutate.mockClear();
  agentList = [
    { name: "claude", kind: "claude_code", description: "Anthropic" },
    { name: "mewbo", kind: "mewbo", description: "research" },
  ];
  resetComposer();
});

describe("Composer", () => {
  it("Enter builds a request with title=first-line, initial_prompt=full, selected agent/model, then routes", async () => {
    const user = userEvent.setup();
    render(<Composer />);

    const textarea = screen.getByTestId("composer-prompt");
    // Type a multi-line prompt: first line → title, full text → initial_prompt.
    await user.click(textarea);
    await user.type(textarea, "Add OAuth login{Shift>}{Enter}{/Shift}with refresh tokens");
    await user.type(textarea, "{Enter}");

    expect(mutate).toHaveBeenCalledTimes(1);
    const req = mutate.mock.calls[0][0] as Record<string, unknown>;
    expect(req.title).toBe("Add OAuth login");
    expect(req.initial_prompt).toBe("Add OAuth login\nwith refresh tokens");
    expect(req.agent_name).toBe("claude"); // defaulted to the first agent
    expect(req.repo_root).toBe("/repos/Grove"); // defaulted to the first project
    expect(req.branch_plan).toEqual({ kind: "auto", base_ref: "HEAD" });
    // The route fires on success with the returned id.
    expect(push).toHaveBeenCalledWith("/w/ws-new");
  });

  it("shows the Model pill for any kind with configured models (claude_code, mewbo); hides it for generic", () => {
    // Default agent is claude_code → Model pill present.
    const { unmount } = render(<Composer />);
    expect(screen.getByTestId("composer-model")).toBeInTheDocument();
    unmount();

    // mewbo now has models in the config JSON (#98) → pill shows, selectable.
    agentList = [{ name: "mewbo", kind: "mewbo", description: "research" }];
    resetComposer({ agentName: "mewbo" });
    const { unmount: unmount2 } = render(<Composer />);
    expect(screen.getByTestId("composer-model")).toBeInTheDocument();
    unmount2();

    // A generic shell has no configured models → no Model pill.
    agentList = [{ name: "shell", kind: "generic", description: "" }];
    resetComposer({ agentName: "shell" });
    render(<Composer />);
    expect(screen.queryByTestId("composer-model")).toBeNull();
  });

  it("retargets the create repo when the sidebar scope changes", async () => {
    render(<Composer />);
    // Picking a repo in the left rail (scopeRepo) shifts the composer's target.
    act(() => useUiStore.getState().setScopeRepo("/repos/Other"));
    await waitFor(() =>
      expect(useUiStore.getState().composer.repoRoot).toBe("/repos/Other"),
    );
  });

  it("opens a fullscreen editor that shares the same draft and offers a Preview tab", async () => {
    const user = userEvent.setup();
    resetComposer({ prompt: "# Plan\n\nbuild the thing" });
    render(<Composer />);

    await user.click(screen.getByTestId("composer-fullscreen"));
    const fs = await screen.findByTestId("composer-prompt-fullscreen");
    expect(fs).toHaveValue("# Plan\n\nbuild the thing"); // shared store draft
    expect(screen.getByTestId("composer-preview-tab")).toBeInTheDocument();
  });

  it("the custom-model-id row sets a free-text model on the request", async () => {
    const user = userEvent.setup();
    resetComposer({ agentName: "claude", prompt: "do a thing" });
    render(<Composer />);

    await user.click(screen.getByTestId("composer-model"));
    const custom = await screen.findByTestId("composer-model-custom");
    await user.type(custom, "claude-opus-4-8{Enter}");
    expect(useUiStore.getState().composer.model).toBe("claude-opus-4-8");

    await user.click(screen.getByTestId("composer-send"));
    const req = mutate.mock.calls[0][0] as Record<string, unknown>;
    expect(req.model).toBe("claude-opus-4-8");
  });

  it("keeps Advanced collapsed by default and reveals branch source + skip-init on toggle", async () => {
    const user = userEvent.setup();
    render(<Composer />);

    expect(screen.queryByTestId("composer-advanced")).toBeNull();
    expect(screen.getByTestId("composer-advanced-toggle")).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    await user.click(screen.getByTestId("composer-advanced-toggle"));
    const advanced = screen.getByTestId("composer-advanced");
    expect(within(advanced).getByTestId("create-mode")).toBeInTheDocument();
    expect(within(advanced).getByTestId("create-skip-init")).toBeInTheDocument();
  });
});
