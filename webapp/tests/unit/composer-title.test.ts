import { describe, it, expect } from "vitest";
import { deriveTitle } from "@/components/composer/composer";

// The composer derives `title` from the prompt client-side; the daemon's
// `CreateWorkspaceRequest.title` caps it at 120 chars (Pydantic max_length,
// src/grove/core/contracts/requests.py). A naturally-typed prompt soft-wraps
// with no hard newline, so an unbounded derivation trips a 422 the moment the
// first line exceeds the cap. These pin the client-side bound.
describe("deriveTitle", () => {
  it("passes short prompts through unchanged", () => {
    expect(deriveTitle("Add OAuth login")).toBe("Add OAuth login");
  });

  it("takes only the first non-empty line of a multi-line prompt", () => {
    expect(deriveTitle("Add OAuth login\nwith refresh tokens")).toBe("Add OAuth login");
    expect(deriveTitle("\n\n  Add OAuth login  \nmore detail")).toBe("Add OAuth login");
  });

  it("leaves a 120-char line untouched (at the cap, not over it)", () => {
    const line = "a".repeat(120);
    expect(deriveTitle(line)).toBe(line);
    expect(deriveTitle(line).length).toBe(120);
  });

  it("truncates a line over 120 chars to <=120, ending in an ellipsis", () => {
    const line = "a".repeat(130);
    const title = deriveTitle(line);
    expect(title.length).toBeLessThanOrEqual(120);
    expect(title.endsWith("…")).toBe(true);
  });

  it("prefers a nearby word boundary over a mid-word hard cut", () => {
    // "word " repeated: a space sits inside the truncation window (last 20
    // chars of the 119-char cut), at index 114 — one clean word short of it.
    const words = "word ".repeat(30); // 150 chars
    const title = deriveTitle(words);
    expect(title).toBe(`${"word ".repeat(22)}word…`);
    expect(title.length).toBeLessThanOrEqual(120);
  });

  it("hard-cuts when no word boundary falls within the window", () => {
    // One giant unbroken word, well over the cap — no space anywhere to snap to.
    const line = "a".repeat(200);
    const title = deriveTitle(line);
    expect(title).toBe(`${"a".repeat(119)}…`);
  });
});
