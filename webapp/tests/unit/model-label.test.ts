import { describe, expect, it } from "vitest";

import { modelLabel } from "@/components/grove/launch/controls/model-pill";
import { customModelError } from "@/lib/grove/adapters/launch";

describe("customModelError", () => {
  it("accepts real provider-id separators and surrounding whitespace", () => {
    expect(customModelError("  anthropic/claude-opus-5.1:beta_test  ")).toBeNull();
  });

  it("refuses empty, non-ASCII, malformed, and oversized ids", () => {
    expect(customModelError(" ")).toBe("Enter a custom model id.");
    expect(customModelError("-claude")).toBe("A custom model id cannot start with a dash.");
    expect(customModelError("claude model")).toContain("ASCII letters");
    expect(customModelError("claudé")).toContain("ASCII letters");
    expect(customModelError("x".repeat(65))).toBe("A custom model id must be at most 64 characters.");
  });
});

describe("modelLabel", () => {
  it("capitalizes plain lowercase words", () => {
    expect(modelLabel("fable")).toBe("Fable");
    expect(modelLabel("sonnet")).toBe("Sonnet");
    expect(modelLabel("opus")).toBe("Opus");
    expect(modelLabel("haiku")).toBe("Haiku");
  });

  it("capitalizes all-letter words separated by spaces and dashes", () => {
    expect(modelLabel("my-model name")).toBe("My-Model Name");
  });

  it("leaves versioned and namespaced provider ids unchanged", () => {
    expect(modelLabel("gpt-4o-mini")).toBe("gpt-4o-mini");
    expect(modelLabel("claude-opus-4-20250514")).toBe("claude-opus-4-20250514");
    expect(modelLabel("anthropic/claude-3.5")).toBe("anthropic/claude-3.5");
  });
});
