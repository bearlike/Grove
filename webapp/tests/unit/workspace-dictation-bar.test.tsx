import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AssistantRuntimeProvider } from "@assistant-ui/react";

import { ControlTip } from "@/components/grove/workspace/composer-tip";
import { DictationBar } from "@/components/grove/workspace/composer";
import { useTranscriptRuntime } from "@/lib/grove/runtime";

vi.mock("@/lib/grove/hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/grove/hooks")>()),
}));

function Harness({ children }: { children: React.ReactNode }) {
  const { runtime } = useTranscriptRuntime({ turns: [] });
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}

describe("the recording bar", () => {
  it("takes its transcribing phase from ABOVE the expand boundary", () => {
    // The dialog move remounts the bar; a local phase would snap a real upload
    // back to "Recording" with a Finish button that has nothing to stop.
    const source = readFileSync("components/grove/workspace/composer.tsx", "utf8");
    expect(source).toContain("<DictationBar transcribing={transcribing} onTranscribing={onTranscribing} />");
    const bar = source.slice(source.indexOf("export function DictationBar"));
    expect(bar).not.toContain("useState(false)");
  });

  const html = renderToStaticMarkup(
    <Harness>
      <DictationBar transcribing={false} onTranscribing={() => {}} />
    </Harness>,
  );

  it("offers cancel, stop and send — in that order — and nothing else", () => {
    const at = (id: string) => html.indexOf(`data-testid="${id}"`);
    expect(at("composer-dictation-cancel")).toBeGreaterThan(-1);
    expect(at("composer-dictation-cancel")).toBeLessThan(at("composer-dictation-done"));
    expect(at("composer-dictation-done")).toBeLessThan(at("composer-dictation-send"));
    expect(html).not.toContain("composer-dictation-transcribing");
    expect(html).not.toContain('data-slot="composer-input"');
    expect(html).not.toContain('data-slot="composer-toolbar"');
  });

  it("draws the LIVE waveform between the controls, with a polite status", () => {
    // Grove's meter, not the vendored 14-bar decoration: every bar is a level
    // the session measured and the strip fills the bar's width.
    expect(html).toContain('data-slot="dictation-waveform"');
    expect(html).not.toContain('data-slot="composer-voice"');
    expect(html).toMatch(/role="status"[^>]*aria-live="polite"[^>]*>Recording</);
  });

  it("names all three controls for assistive tech", () => {
    expect(html).toContain('aria-label="Cancel dictation"');
    expect(html).toContain('aria-label="Finish dictation"');
    expect(html).toContain('aria-label="Send dictated message"');
  });
});

describe("ControlTip", () => {
  it("wraps the control in a span, so the control keeps its own data-slot", () => {
    const html = renderToStaticMarkup(
      <ControlTip tip="Add attachment">
        <button data-slot="composer-attach" type="button" />
      </ControlTip>,
    );
    expect(html).toMatch(/<span[^>]*data-slot="tooltip-trigger"[^>]*><button data-slot="composer-attach"/);
  });
});
