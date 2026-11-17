import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LAUNCH_PRESETS } from "@/components/grove/launch/launch-presets";
import { LaunchSuggestions } from "@/components/grove/launch/launch-suggestions";

describe("the landing suggestions", () => {
  const markup = renderToStaticMarkup(<LaunchSuggestions onTry={() => {}} />);

  it("names the section and draws exactly the presets, one card and one Try each", () => {
    expect(markup).toContain("See what Grove can do");
    expect(LAUNCH_PRESETS).toHaveLength(3);
    for (const { id, title } of LAUNCH_PRESETS) {
      expect(markup).toContain(`data-testid="launch-suggestion-${id}"`);
      expect(markup).toContain(`aria-label="Try: ${title}"`);
    }
    expect(markup.match(/launch-try-/g)).toHaveLength(LAUNCH_PRESETS.length);
  });

  it("offers actionable coding prompts with distinct ids", () => {
    expect(new Set(LAUNCH_PRESETS.map(({ id }) => id)).size).toBe(LAUNCH_PRESETS.length);
    for (const { prompt, description } of LAUNCH_PRESETS) {
      expect(prompt.length).toBeGreaterThan(description.length);
    }
  });

  it("gives each card a COLOURED mark at twice the control size, a bold title and a normal body", () => {
    // The reference's cards each lead with their own coloured mark; the hue is
    // an IDENTITY (§4.1), so every preset names one from the chart set and no
    // two presets share it. The title ranks above the body by weight alone.
    const tones = LAUNCH_PRESETS.map(({ tone }) => tone);
    expect(new Set(tones).size).toBe(tones.length);
    for (const tone of tones) expect(tone).toMatch(/^text-chart-[1-5]$/);
    expect(markup.match(/data-slot="launch-suggestion-mark"/g)).toHaveLength(LAUNCH_PRESETS.length);
    const marks = markup.match(/<svg[^>]*data-slot="launch-suggestion-mark"[^>]*>/g) ?? [];
    expect(marks).toHaveLength(LAUNCH_PRESETS.length);
    // `size-6`, not the first cut's `size-8`: a coloured glyph reads larger than a grey one.
    for (const mark of marks) expect(mark).toMatch(/class="[^"]*\bsize-6\b[^"]*\btext-chart-[1-5]\b/);
    const tag = (slot: string) => markup.match(new RegExp(`<p[^>]*data-slot="${slot}"[^>]*>`, "g")) ?? [];
    expect(tag("launch-suggestion-title")).toHaveLength(LAUNCH_PRESETS.length);
    for (const title of tag("launch-suggestion-title")) expect(title).toMatch(/\bfont-semibold\b/);
    for (const body of tag("launch-suggestion-body")) expect(body).toMatch(/\bfont-normal\b/);
  });

  it("draws Try as a PILL — the vendored outline button, rounded full by the theme", () => {
    const tries = markup.match(/<button[^>]*data-testid="launch-try-[^"]*"[^>]*>/g) ?? [];
    expect(tries).toHaveLength(LAUNCH_PRESETS.length);
    for (const button of tries) expect(button).toContain('data-variant="outline"');
    // Radius is the theme's (lint:styling forbids it at a call site).
    const css = readFileSync("app/globals.css", "utf8");
    expect(css).toMatch(/\.launch-suggestion \[data-testid\^="launch-try-"\]\s*\{[^}]*border-radius:\s*9999px/);
  });

  it("leaves extra room below the text, so every card reads taller than its words", () => {
    // Even padding sat the description flush against the card's bottom edge.
    const cards = markup.match(/<div[^>]*data-testid="launch-suggestion-[^"]*"[^>]*>/g) ?? [];
    expect(cards).toHaveLength(LAUNCH_PRESETS.length);
    for (const card of cards) {
      expect(card).toMatch(/\bpb-8\b/);
      expect(card).toMatch(/\bmin-h-44\b/);
    }
  });

  it("exposes the theme hooks and leaves paint to the theme", () => {
    expect(markup).toContain("launch-suggestions");
    expect(markup).toContain("launch-suggestion ");
    expect(markup).toContain("launch-suggestions-heading");
  });
});

describe("Try on the landing surface", () => {
  const surface = readFileSync("components/grove/launch/launch-surface.tsx", "utf8");
  const start = surface.indexOf("const tryPreset");
  const body = surface.slice(start, surface.indexOf("return (", start));

  it("fills an empty brief, joins a typed one, focuses the editor, and never submits", () => {
    expect(start).toBeGreaterThan(-1);
    // A functional update over the CURRENT draft: a typed paragraph is never
    // replaced by a click on Try, with no undo.
    expect(body).toContain("launch.setPrompt((current) =>");
    expect(body).toMatch(/current\.trim\(\) \? `\$\{current\.trimEnd\(\)\}\\n\\n\$\{prompt\}` : prompt/);
    expect(body).toContain(".focus()");
    expect(body).not.toContain("submit");
  });

  it("takes its presets from the one list", () => {
    expect(surface).toContain("<LaunchSuggestions onTry={tryPreset} />");
    expect(surface).not.toContain("LAUNCH_PRESETS");
  });
});
