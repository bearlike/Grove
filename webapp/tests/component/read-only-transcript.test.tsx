import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReadOnlyTranscript } from "@/components/chat/read-only-transcript";
import type { SessionTurnView } from "@/lib/grove/types";

/**
 * The catalog drill-in's rendering contract. Two things matter and both
 * are structural, not cosmetic: it renders through the SAME assistant-ui path
 * the live panel uses (so a historical transcript reads identically), and it
 * offers NO way to write — a catalog session usually has no workspace behind it,
 * so a composer would promise an affordance the wire cannot honor.
 */
const TURNS: SessionTurnView[] = [
  {
    user_text: "map the webapp directory",
    started_at: "2026-07-20T10:00:00Z",
    entries: [{ role: "assistant", text: "Here is the map.", question: null, file_edit: null, todo: null }],
  },
];

describe("ReadOnlyTranscript", () => {
  it("renders the recorded turns through the shared chat-message path", () => {
    render(<ReadOnlyTranscript turns={TURNS} />);

    expect(screen.getByTestId("read-only-transcript")).toBeInTheDocument();
    expect(screen.getByText("map the webapp directory")).toBeInTheDocument();
    expect(screen.getByText("Here is the map.")).toBeInTheDocument();
    expect(screen.getAllByTestId("chat-message").length).toBeGreaterThan(0);
  });

  it("offers no composer, no send, and no interrupt", () => {
    render(<ReadOnlyTranscript turns={TURNS} />);

    expect(screen.queryByTestId("chat-composer")).toBeNull();
    expect(screen.queryByTestId("chat-interrupt")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /send/i })).toBeNull();
  });

  it("says a transcript with no turns is empty rather than rendering nothing", () => {
    render(<ReadOnlyTranscript turns={[]} />);

    expect(screen.getByTestId("read-only-transcript-empty")).toHaveTextContent(
      "No conversation recorded",
    );
  });
});
