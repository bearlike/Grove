import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CHUNK_MS, DictationWaveform, WAVEFORM_BAR_PITCH, waveformBars } from "@/components/grove/dictation-waveform";

describe("the live dictation waveform", () => {
  it("draws one bar per level, newest on the right, sized by the level", () => {
    const html = renderToStaticMarkup(<DictationWaveform levels={[0, 0.5, 1]} />);
    const bars = html.match(/<span[^>]*data-slot="dictation-bar"[^>]*>/g) ?? [];
    expect(bars).toHaveLength(3);
    // The CSS variable carries the level; the theme turns it into a height.
    expect(bars[0]).toContain("--level:0;");
    expect(bars[1]).toContain("--level:0.5;");
    expect(bars[2]).toContain("--level:1;");
  });

  it("is decoration beside the status line, never announced bar by bar", () => {
    const html = renderToStaticMarkup(<DictationWaveform levels={[0.2]} />);
    expect(html).toMatch(/<div[^>]*aria-hidden="true"[^>]*data-slot="dictation-waveform"/);
    expect(html).not.toContain('role="img"');
  });

  it("fits as many bars as the strip's width allows, never fewer than one", () => {
    expect(waveformBars(0)).toBe(1);
    expect(waveformBars(WAVEFORM_BAR_PITCH * 40)).toBe(40);
    expect(waveformBars(WAVEFORM_BAR_PITCH * 40 + 1)).toBe(40);
  });

  it("is as dense as the reference and flows: a chunk every 60 ms at a 7px pitch, ~6 s per 700px", () => {
    // 28px/800ms read as "broken teeth"; the reference is ~3px bars at ~7px,
    // and the cadence was raised 4x from 250 ms on request.
    expect(CHUNK_MS).toBe(60);
    expect(WAVEFORM_BAR_PITCH).toBe(7);
    const seconds = (waveformBars(700) * CHUNK_MS) / 1000;
    expect(seconds).toBeGreaterThanOrEqual(5);
    expect(seconds).toBeLessThanOrEqual(7);
  });

  it("samples with requestAnimationFrame while live and stops when not", () => {
    // The hook is the only place a frame loop may live, and it must end with
    // the recording: a loop left running is a meter polling a dead session.
    const source = readFileSync("components/grove/dictation-waveform.tsx", "utf8");
    expect(source).toContain("requestAnimationFrame");
    expect(source).toContain("cancelAnimationFrame");
    expect(source).toContain("ResizeObserver");
  });
});

describe("the two recording bars share one motion contract", () => {
  // The theme animates ONE slot through its phases, so both surfaces move
  // identically; a bar that forgot the slot would swap states with no motion
  // and nothing but a screen recording would notice.
  for (const file of [
    "components/grove/launch/launch-dictation.tsx",
    "components/grove/workspace/composer.tsx",
  ]) {
    it(`${file} marks its bar with the dictation slot and its phase`, () => {
      const source = readFileSync(file, "utf8");
      expect(source).toContain('data-slot="dictation"');
      expect(source).toMatch(/data-phase=\{[^}]*"recording"[^}]*"transcribing"[^}]*\}/);
    });
  }

  it("the theme owns the motion: enter, phase, newest-bar emphasis, and a reduced-motion opt-out", () => {
    const css = readFileSync("app/globals.css", "utf8");
    expect(css).toMatch(/\[data-slot="dictation"\]\s*\{[^}]*@starting-style/s);
    expect(css).toMatch(/\[data-slot="dictation"\]\[data-phase="transcribing"\]/);
    expect(css).toMatch(/\[data-slot="dictation-bar"\]:last-child/);
    expect(css).toMatch(/prefers-reduced-motion: reduce\)[^}]*\[data-slot="dictation/s);
  });
});
