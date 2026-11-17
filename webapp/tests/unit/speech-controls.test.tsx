import {
  AssistantRuntimeProvider,
  MessagePrimitive,
  ThreadPrimitive,
  type DictationAdapter,
  type SpeechSynthesisAdapter,
} from "@assistant-ui/react";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  LaunchDictateButton,
  LaunchVoiceView,
  useLaunchDictation,
} from "@/components/grove/launch/launch-dictation";
import { Dictation } from "@/components/grove/workspace/composer";
import { TurnActions } from "@/components/grove/workspace/turn-actions";
import type { DigestEntryView, SessionTurnView } from "@/lib/grove/api";
import { useTranscriptRuntime } from "@/lib/grove/runtime";
import { useSpeechAdapters } from "@/lib/grove/runtime/speech";

const mocks = vi.hoisted(() => ({ speech: undefined as boolean | undefined }));
vi.mock("@/lib/grove/hooks", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/grove/hooks")>()),
  useWhoami: () => ({ data: mocks.speech === undefined ? undefined : { speech: mocks.speech } }),
}));

const TURN: SessionTurnView = {
  user_text: "go",
  started_at: "2026-06-09T08:00:00Z",
  entries: [{ role: "assistant", text: "done" }] as DigestEntryView[],
};

const DICTATION: DictationAdapter = {
  listen: () => {
    throw new Error("not under test");
  },
};
const SYNTHESIS: SpeechSynthesisAdapter = {
  speak: () => {
    throw new Error("not under test");
  },
};

function Harness({ speech, children }: { speech: boolean; children: React.ReactNode }) {
  const { runtime } = useTranscriptRuntime({
    turns: [TURN],
    ...(speech ? { speech: { dictation: DICTATION, speech: SYNTHESIS } } : {}),
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Messages>
        {() => <MessagePrimitive.Root>{children}</MessagePrimitive.Root>}
      </ThreadPrimitive.Messages>
    </AssistantRuntimeProvider>
  );
}

beforeEach(() => {
  mocks.speech = undefined;
});

describe("the read-aloud button", () => {
  it("renders under an answer only when the runtime carries a speech adapter", () => {
    const on = renderToStaticMarkup(
      <Harness speech>
        <TurnActions />
      </Harness>,
    );
    const off = renderToStaticMarkup(
      <Harness speech={false}>
        <TurnActions />
      </Harness>,
    );
    expect(on).toContain("lucide-volume-2");
    expect(on).toContain("Read aloud");
    // Idle, so the stop control is not mounted beside it.
    expect(on).not.toContain("Stop reading");
    expect(off).not.toContain("lucide-volume-2");
    // Copy is unaffected either way.
    expect(off).toContain("lucide-copy");
  });
});

describe("the composer's dictation control", () => {
  it("renders the mic only when the runtime carries a dictation adapter", () => {
    const on = renderToStaticMarkup(
      <Harness speech>
        <Dictation />
      </Harness>,
    );
    const off = renderToStaticMarkup(
      <Harness speech={false}>
        <Dictation />
      </Harness>,
    );
    expect(on).toContain('data-testid="composer-dictate"');
    expect(on).toContain("lucide-mic");
    // Idle: neither the recording pair nor the spinner.
    expect(on).not.toContain("composer-dictation-cancel");
    expect(on).not.toContain("composer-dictation-transcribing");
    expect(off).not.toContain("composer-dictate");
  });
});

describe("the landing composer's dictation", () => {
  function Probe() {
    const dictation = useLaunchDictation(() => {}, () => {});
    return <LaunchDictateButton available={dictation.available} onStart={dictation.start} />;
  }
  const render = () => renderToStaticMarkup(<Probe />);

  it("is hidden until whoami reports a speech provider", () => {
    expect(render()).toBe("");
    mocks.speech = false;
    expect(render()).toBe("");
  });

  it("offers the mic when speech is configured, and nothing else while idle", () => {
    mocks.speech = true;
    const markup = render();
    expect(markup).toContain('data-testid="launch-dictate"');
    expect(markup).not.toContain("launch-dictation-cancel");
    expect(markup).not.toContain("launch-dictation-transcribing");
  });

  it("recording replaces the body: cancel, the live waveform, stop, then send", () => {
    const markup = renderToStaticMarkup(
      <LaunchVoiceView phase="recording" level={() => 0} onCancel={() => {}} onDone={() => {}} onSend={() => {}} />,
    );
    const at = (id: string) => markup.indexOf(id);
    expect(at("launch-dictation-cancel")).toBeLessThan(at('data-slot="dictation-waveform"'));
    expect(at('data-slot="dictation-waveform"')).toBeLessThan(at("launch-dictation-done"));
    expect(at("launch-dictation-done")).toBeLessThan(at("launch-dictation-send"));
    expect(markup).not.toContain('data-slot="composer-voice"');
    expect(markup).toMatch(/role="status"[^>]*>Recording/);
  });

  it("transcribing keeps Cancel live and replaces Stop with a busy mark", () => {
    // The adapter aborts an in-flight upload on cancel (the reader no longer
    // wants the words), so Cancel stays; Stop has nothing left to stop.
    const markup = renderToStaticMarkup(
      <LaunchVoiceView phase="transcribing" level={() => 0} onCancel={() => {}} onDone={() => {}} onSend={() => {}} />,
    );
    expect(markup).toContain('data-testid="launch-dictation-transcribing"');
    expect(markup).toContain('data-testid="launch-dictation-cancel"');
    expect(markup).not.toContain('data-testid="launch-dictation-done"');
    expect(markup).not.toContain('data-testid="launch-dictation-send"');
    expect(markup).toContain("aria-busy");
    expect(markup).not.toMatch(/disabled=""/);
  });
});

describe("the adapters are gated on whoami.speech", () => {
  function Probe({ available }: { available: boolean }) {
    const speech = useSpeechAdapters(available, () => {});
    return <span data-has={speech ? "yes" : "no"} />;
  }

  it("builds none without a configured provider key, so no control is ever offered", () => {
    expect(renderToStaticMarkup(<Probe available={false} />)).toContain('data-has="no"');
    expect(renderToStaticMarkup(<Probe available />)).toContain('data-has="yes"');
  });

  it("the steerable thread feeds the gate from whoami, and only that", () => {
    const source = readFileSync("lib/grove/runtime/thread.ts", "utf8");
    expect(source).toContain("useSpeechAdapters(whoami.data?.speech === true, setNotice)");
  });
});
