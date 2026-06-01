import { describe, it, expect } from "vitest";
import { stripAnsi } from "@/lib/grove/ansi";

describe("stripAnsi", () => {
  it("strips a basic SGR color escape", () => {
    expect(stripAnsi("\x1b[31mred\x1b[0m")).toBe("red");
  });

  it("strips 256-color and truecolor escapes", () => {
    expect(stripAnsi("\x1b[38;5;114m●\x1b[39m")).toBe("●");
    expect(stripAnsi("\x1b[38;2;132;204;22mlive\x1b[0m")).toBe("live");
  });

  it("strips cursor-move CSI sequences", () => {
    expect(stripAnsi("a\x1b[2Ab")).toBe("ab");
  });

  it("strips OSC hyperlink sequences (BEL terminator)", () => {
    expect(stripAnsi("\x1b]8;;https://example.com\x07link\x1b]8;;\x07")).toBe("link");
  });

  it("leaves plain text untouched", () => {
    expect(stripAnsi("plain text\nline 2")).toBe("plain text\nline 2");
  });
});
