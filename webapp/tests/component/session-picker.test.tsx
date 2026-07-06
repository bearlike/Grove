import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SessionPicker, SessionPickerList } from "@/components/chat/session-picker";
import type { SessionSummaryView } from "@/lib/grove/types";

// Pure presentational component — no hooks, no fetch, so these tests exercise
// the public prop contract directly (render, select, make-primary, pending,
// error) without a QueryClientProvider.

function session(overrides: Partial<SessionSummaryView>): SessionSummaryView {
  return {
    session_id: "s1111112222233333",
    adapter_kind: "claude_code",
    provenance: "grove_launched",
    workspace_id: "w1",
    workspace_title: "feat depth",
    workspace_branch: "feat/depth",
    git_branch: "feat/depth",
    created_at: "2026-06-10T09:00:00Z",
    modified_at: "2026-06-10T11:00:00Z",
    size_bytes: 4096,
    title: "wire the panel",
    first_prompt: "build the panel",
    last_prompt: "ship it",
    activity: {
      state: "working",
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
      questions: [],
    },
    ...overrides,
  };
}

const PRIMARY = session({ session_id: "s-primary-aaaa", title: "wire the panel" });
const OTHER = session({
  session_id: "s-other-bbbb",
  title: null,
  first_prompt: "explore the codebase",
  modified_at: "2026-06-09T08:00:00Z",
  activity: { ...PRIMARY.activity, state: "idle" },
});
// The ungated remap-picker candidate (#132): the dead-pointer's live successor
// the adoption gate drops, offered so the human can pin it.
const CANDIDATE = session({
  session_id: "s-candidate-cccc",
  title: null,
  first_prompt: "the live successor conversation",
  activity: { ...PRIMARY.activity, state: "idle" },
});

describe("SessionPicker", () => {
  it("renders nothing when there is nothing to switch between and no candidates", () => {
    const { container } = render(
      <SessionPicker
        sessions={[PRIMARY]}
        selectedId={PRIMARY.session_id}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={null}
        error={null}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("the trigger shows the selected session's short id and state", () => {
    render(
      <SessionPicker
        sessions={[PRIMARY, OTHER]}
        selectedId={PRIMARY.session_id}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={null}
        error={null}
      />,
    );
    const trigger = screen.getByTestId("session-picker-trigger");
    expect(trigger).toHaveTextContent("s-prima");
  });

  it("lists every session with the selected row marked", async () => {
    const user = userEvent.setup();
    render(
      <SessionPicker
        sessions={[PRIMARY, OTHER]}
        selectedId={PRIMARY.session_id}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={null}
        error={null}
      />,
    );
    await user.click(screen.getByTestId("session-picker-trigger"));

    const items = await screen.findAllByTestId("session-picker-item");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveAttribute("data-session-id", "s-primary-aaaa");
    expect(items[0]).toHaveAttribute("data-selected", "true");
    expect(items[1]).toHaveAttribute("data-selected", "false");
    expect(within(items[1]).getByText("explore the codebase")).toBeInTheDocument();
  });

  it("clicking a row's select control switches the view, without a make-primary call", async () => {
    const onSelect = vi.fn();
    const onMakePrimary = vi.fn();
    const user = userEvent.setup();
    render(
      <SessionPicker
        sessions={[PRIMARY, OTHER]}
        selectedId={PRIMARY.session_id}
        onSelect={onSelect}
        onMakePrimary={onMakePrimary}
        pendingId={null}
        error={null}
      />,
    );
    await user.click(screen.getByTestId("session-picker-trigger"));
    const items = await screen.findAllByTestId("session-picker-item");
    await user.click(within(items[1]).getByTestId("session-picker-select"));

    expect(onSelect).toHaveBeenCalledWith("s-other-bbbb");
    expect(onMakePrimary).not.toHaveBeenCalled();
  });

  it("'Make primary' only appears on non-selected rows and dispatches that session's id", async () => {
    const onMakePrimary = vi.fn();
    const user = userEvent.setup();
    render(
      <SessionPicker
        sessions={[PRIMARY, OTHER]}
        selectedId={PRIMARY.session_id}
        onSelect={vi.fn()}
        onMakePrimary={onMakePrimary}
        pendingId={null}
        error={null}
      />,
    );
    await user.click(screen.getByTestId("session-picker-trigger"));
    const items = await screen.findAllByTestId("session-picker-item");

    expect(within(items[0]).queryByTestId("session-picker-make-primary")).toBeNull();
    const makePrimary = within(items[1]).getByTestId("session-picker-make-primary");
    await user.click(makePrimary);
    expect(onMakePrimary).toHaveBeenCalledWith("s-other-bbbb");
  });

  it("disables and labels the pending row's make-primary button", async () => {
    const user = userEvent.setup();
    render(
      <SessionPicker
        sessions={[PRIMARY, OTHER]}
        selectedId={PRIMARY.session_id}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={OTHER.session_id}
        error={null}
      />,
    );
    await user.click(screen.getByTestId("session-picker-trigger"));
    const items = await screen.findAllByTestId("session-picker-item");
    const makePrimary = within(items[1]).getByTestId("session-picker-make-primary");
    expect(makePrimary).toBeDisabled();
    expect(makePrimary).toHaveTextContent("Pinning…");
  });

  it("renders a refusal notice at the foot of the open popover (#130)", async () => {
    const user = userEvent.setup();
    render(
      <SessionPicker
        sessions={[PRIMARY, OTHER]}
        selectedId={PRIMARY.session_id}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={null}
        error="no unique session for that prefix"
      />,
    );
    // The notice moved INSIDE the popover (a header-anchored trigger can't float
    // a sibling notice), so it's only in the DOM once the popover is open.
    await user.click(screen.getByTestId("session-picker-trigger"));
    expect(await screen.findByTestId("session-picker-notice")).toHaveTextContent(
      "no unique session for that prefix",
    );
  });

  // ── Track mode (#132): the escape hatch when the workspace tracks a dead
  //    pointer and the switcher would otherwise self-hide with nothing to offer.
  it("offers a track affordance when none is tracked but a candidate exists", async () => {
    const user = userEvent.setup();
    render(
      <SessionPicker
        sessions={[]}
        candidates={[CANDIDATE]}
        selectedId={null}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={null}
        error={null}
      />,
    );
    const trigger = screen.getByTestId("session-picker-trigger");
    expect(trigger).toHaveTextContent("Track a session");

    await user.click(trigger);
    const rows = await screen.findAllByTestId("session-picker-candidate");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute("data-session-id", "s-candidate-cccc");
    expect(within(rows[0]).getByText("the live successor conversation")).toBeInTheDocument();
    // Not a switcher — no client-side "select/switch" control in track mode.
    expect(screen.queryByTestId("session-picker-item")).toBeNull();
  });

  it("tracking a candidate dispatches its id to onMakePrimary (adopt = remap), never onSelect", async () => {
    const onMakePrimary = vi.fn();
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <SessionPicker
        sessions={[]}
        candidates={[CANDIDATE]}
        selectedId={null}
        onSelect={onSelect}
        onMakePrimary={onMakePrimary}
        pendingId={null}
        error={null}
      />,
    );
    await user.click(screen.getByTestId("session-picker-trigger"));
    await user.click(screen.getByTestId("session-picker-track"));
    expect(onMakePrimary).toHaveBeenCalledWith("s-candidate-cccc");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("disables and labels the pending candidate's track button", async () => {
    const user = userEvent.setup();
    render(
      <SessionPicker
        sessions={[]}
        candidates={[CANDIDATE]}
        selectedId={null}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={CANDIDATE.session_id}
        error={null}
      />,
    );
    await user.click(screen.getByTestId("session-picker-trigger"));
    const track = screen.getByTestId("session-picker-track");
    expect(track).toBeDisabled();
    expect(track).toHaveTextContent("Tracking…");
  });

  it("prefers the multi-session switcher over the track affordance when >1 session is tracked", async () => {
    const user = userEvent.setup();
    render(
      <SessionPicker
        sessions={[PRIMARY, OTHER]}
        candidates={[CANDIDATE]}
        selectedId={PRIMARY.session_id}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={null}
        error={null}
      />,
    );
    // A real switch exists, so candidates are ignored: the switcher trigger + its
    // rows render, and no candidate row appears.
    expect(screen.getByTestId("session-picker-trigger")).toHaveTextContent("s-prima");
    await user.click(screen.getByTestId("session-picker-trigger"));
    expect(screen.getAllByTestId("session-picker-item")).toHaveLength(2);
    expect(screen.queryByTestId("session-picker-candidate")).toBeNull();
  });
});

// The list body is exported without the Popover shell so it can drop straight
// into the header identity popover's Sessions section. It renders its rows
// inline (no trigger, no click) and keeps every row seam.
describe("SessionPickerList (shell-less body)", () => {
  it("renders the switch rows inline, with no trigger or popover wrapper", () => {
    const onSelect = vi.fn();
    render(
      <SessionPickerList
        sessions={[PRIMARY, OTHER]}
        selectedId={PRIMARY.session_id}
        onSelect={onSelect}
        onMakePrimary={vi.fn()}
        pendingId={null}
        error={null}
      />,
    );
    // No wrapper chrome — the list is the whole surface here.
    expect(screen.queryByTestId("session-picker")).toBeNull();
    expect(screen.queryByTestId("session-picker-trigger")).toBeNull();

    const items = screen.getAllByTestId("session-picker-item");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveAttribute("data-session-id", "s-primary-aaaa");
    expect(items[0]).toHaveAttribute("data-selected", "true");
  });

  it("dispatches select / make-primary from the inline rows", async () => {
    const onSelect = vi.fn();
    const onMakePrimary = vi.fn();
    const user = userEvent.setup();
    render(
      <SessionPickerList
        sessions={[PRIMARY, OTHER]}
        selectedId={PRIMARY.session_id}
        onSelect={onSelect}
        onMakePrimary={onMakePrimary}
        pendingId={null}
        error={null}
      />,
    );
    const items = screen.getAllByTestId("session-picker-item");
    await user.click(within(items[1]).getByTestId("session-picker-select"));
    expect(onSelect).toHaveBeenCalledWith("s-other-bbbb");
    await user.click(within(items[1]).getByTestId("session-picker-make-primary"));
    expect(onMakePrimary).toHaveBeenCalledWith("s-other-bbbb");
  });

  it("renders inline candidate rows + the foot notice in track mode", () => {
    render(
      <SessionPickerList
        sessions={[]}
        candidates={[CANDIDATE]}
        selectedId={null}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={null}
        error="no unique session for that prefix"
      />,
    );
    expect(screen.getByTestId("session-picker-candidate")).toHaveAttribute(
      "data-session-id",
      "s-candidate-cccc",
    );
    expect(screen.getByTestId("session-picker-notice")).toHaveTextContent(
      "no unique session for that prefix",
    );
  });

  it("returns null when there is neither a switch nor a track to offer", () => {
    const { container } = render(
      <SessionPickerList
        sessions={[PRIMARY]}
        selectedId={PRIMARY.session_id}
        onSelect={vi.fn()}
        onMakePrimary={vi.fn()}
        pendingId={null}
        error={null}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
