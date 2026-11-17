import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GroveDictationAdapter, GroveSpeechAdapter } from "@/lib/grove/runtime/speech";

/** A recorder that records nothing and reports stop the way the browser does. */
class FakeRecorder {
  static instances: FakeRecorder[] = [];
  static isTypeSupported = (type: string): boolean => type === "audio/webm;codecs=opus";
  state: "inactive" | "recording" = "inactive";
  mimeType: string;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  constructor(
    readonly stream: unknown,
    options?: { mimeType?: string },
  ) {
    this.mimeType = options?.mimeType ?? "audio/webm";
    FakeRecorder.instances.push(this);
  }
  start(): void {
    this.state = "recording";
  }
  stop(): void {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["voice"], { type: this.mimeType }) });
    this.onstop?.();
  }
}

const tracks: { stop: ReturnType<typeof vi.fn> }[] = [];
let grant: (() => void) | null = null;

function stubBrowser(options: { deferGrant?: boolean; deny?: boolean } = {}): void {
  tracks.length = 0;
  FakeRecorder.instances = [];
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  vi.stubGlobal("navigator", {
    mediaDevices: {
      getUserMedia: vi.fn(async () => {
        if (options.deny) throw new Error("denied");
        const track = { stop: vi.fn() };
        tracks.push(track);
        const stream = { getTracks: () => [track] };
        if (options.deferGrant) await new Promise<void>((resolve) => (grant = resolve));
        return stream;
      }),
    },
  });
}

const flush = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  grant = null;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function setup() {
  const client = {
    transcribeSpeech: vi.fn(async (_audio: string, _filename: string) => ({ text: "hello world" })),
    synthesizeSpeech: vi.fn(async (_text: string, _signal?: AbortSignal) => new Blob(["audio"])),
  };
  const onError = vi.fn();
  return { client, onError, adapter: new GroveDictationAdapter(client, onError) };
}

describe("dictation: cancel", () => {
  it("makes NO request, emits nothing and releases the microphone", async () => {
    stubBrowser();
    const { client, adapter } = setup();
    const session = adapter.listen();
    const spoken = vi.fn();
    session.onSpeech(spoken);
    await flush();
    expect(session.status.type).toBe("running");

    adapter.cancel();

    expect(client.transcribeSpeech).not.toHaveBeenCalled();
    expect(spoken).not.toHaveBeenCalled();
    expect(session.status).toEqual({ type: "ended", reason: "cancelled" });
    expect(tracks.every((track) => track.stop.mock.calls.length > 0)).toBe(true);
    expect(tracks).toHaveLength(1);
  });

  it("releases a microphone granted AFTER the cancel, and starts no recorder", async () => {
    stubBrowser({ deferGrant: true });
    const { client, adapter } = setup();
    const session = adapter.listen();
    await flush();
    adapter.cancel();
    grant?.();
    await flush();
    expect(tracks).toHaveLength(1);
    expect(tracks[0]!.stop).toHaveBeenCalled();
    expect(FakeRecorder.instances).toHaveLength(0);
    expect(client.transcribeSpeech).not.toHaveBeenCalled();
    expect(session.status.type).toBe("ended");
  });

  it("a cancel during the upload drops the transcript instead of delivering it", async () => {
    stubBrowser();
    const { client, adapter } = setup();
    let finishUpload: (value: { text: string }) => void = () => {};
    client.transcribeSpeech.mockImplementation(
      () => new Promise((resolve) => (finishUpload = resolve)),
    );
    const session = adapter.listen();
    const spoken = vi.fn();
    session.onSpeech(spoken);
    await flush();
    const stopped = session.stop();
    await flush();
    adapter.cancel();
    finishUpload({ text: "too late" });
    await stopped;
    expect(spoken).not.toHaveBeenCalled();
    expect(session.status).toEqual({ type: "ended", reason: "cancelled" });
  });
});

describe("dictation: stop", () => {
  it("uploads exactly once and emits ONE final transcript BEFORE the status ends", async () => {
    stubBrowser();
    const { client, adapter } = setup();
    const session = adapter.listen();
    const order: string[] = [];
    const results: unknown[] = [];
    session.onSpeech((result) => {
      // assistant-ui tears the session down the moment it reads `ended`, so the
      // transcript must already be out while the status is still live.
      order.push(`speech:${session.status.type}`);
      results.push(result);
    });
    session.onSpeechEnd(() => order.push(`end:${session.status.type}`));
    await flush();

    await Promise.all([session.stop(), session.stop()]);

    expect(client.transcribeSpeech).toHaveBeenCalledTimes(1);
    expect(client.transcribeSpeech.mock.calls[0]![1]).toBe("speech.webm");
    expect(results).toEqual([{ transcript: "hello world", isFinal: true }]);
    expect(order).toEqual(["speech:running", "end:ended"]);
    expect(session.status).toEqual({ type: "ended", reason: "stopped" });
    expect(tracks[0]!.stop).toHaveBeenCalled();
  });

  it("stays non-ended for the whole upload", async () => {
    stubBrowser();
    const { client, adapter } = setup();
    let finishUpload: (value: { text: string }) => void = () => {};
    client.transcribeSpeech.mockImplementation(
      () => new Promise((resolve) => (finishUpload = resolve)),
    );
    const session = adapter.listen();
    await flush();
    const stopped = session.stop();
    await flush();
    expect(session.status.type).toBe("running");
    finishUpload({ text: "done" });
    await stopped;
    expect(session.status.type).toBe("ended");
  });
});

describe("dictation: failure", () => {
  it("a transcription error ends the session, releases the microphone and reports once", async () => {
    stubBrowser();
    const { client, adapter, onError } = setup();
    client.transcribeSpeech.mockRejectedValue(new Error("provider down"));
    const session = adapter.listen();
    const spoken = vi.fn();
    session.onSpeech(spoken);
    await flush();
    await session.stop();
    expect(spoken).not.toHaveBeenCalled();
    expect(session.status).toEqual({ type: "ended", reason: "error" });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(tracks[0]!.stop).toHaveBeenCalled();
  });

  it("a recorder that cannot start still gives the granted microphone back", async () => {
    stubBrowser();
    vi.stubGlobal(
      "MediaRecorder",
      class {
        static isTypeSupported = () => true;
        constructor() {
          throw new Error("unsupported container");
        }
      },
    );
    const { adapter, onError } = setup();
    const session = adapter.listen();
    await flush();
    expect(session.status).toEqual({ type: "ended", reason: "error" });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(tracks).toHaveLength(1);
    expect(tracks[0]!.stop).toHaveBeenCalled();
  });

  it("a denied microphone ends the session as an error with nothing to release", async () => {
    stubBrowser({ deny: true });
    const { client, adapter, onError } = setup();
    const session = adapter.listen();
    await flush();
    expect(session.status).toEqual({ type: "ended", reason: "error" });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(client.transcribeSpeech).not.toHaveBeenCalled();
  });
});

/** An Audio double: plays until told it ended, and records what it was given. */
class FakeAudio {
  static instances: FakeAudio[] = [];
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  paused = false;
  constructor(readonly src: string) {
    FakeAudio.instances.push(this);
  }
  play(): Promise<void> {
    return Promise.resolve();
  }
  pause(): void {
    this.paused = true;
  }
}

describe("read-aloud", () => {
  const revoked: string[] = [];
  let urlCount = 0;
  beforeEach(() => {
    FakeAudio.instances = [];
    revoked.length = 0;
    urlCount = 0;
    vi.stubGlobal("Audio", FakeAudio);
    vi.stubGlobal("URL", {
      createObjectURL: () => `blob:${(urlCount += 1)}`,
      revokeObjectURL: (url: string) => revoked.push(url),
    });
  });

  it("starts, runs, and finishes after the last chunk, revoking every URL", async () => {
    const { client, onError } = setup();
    const speech = new GroveSpeechAdapter(client, onError);
    const longText = `${"word ".repeat(1500)}`.trim(); // > 4096 chars => 2 chunks
    const utterance = speech.speak(longText);
    expect(utterance.status.type).toBe("starting");
    await flush();
    expect(utterance.status.type).toBe("running");
    // The second chunk is requested while the first is still playing.
    expect(client.synthesizeSpeech).toHaveBeenCalledTimes(2);
    FakeAudio.instances[0]!.onended?.();
    await flush();
    FakeAudio.instances[1]!.onended?.();
    await flush();
    expect(utterance.status).toEqual({ type: "ended", reason: "finished" });
    expect(revoked).toEqual(["blob:1", "blob:2"]);
    expect(onError).not.toHaveBeenCalled();
  });

  it("cancel stops playback, aborts the request and never reports an error", async () => {
    const { client, onError } = setup();
    const signals: AbortSignal[] = [];
    client.synthesizeSpeech.mockImplementation(async (_text: string, signal?: AbortSignal) => {
      if (signal) signals.push(signal);
      return new Blob(["audio"]);
    });
    const speech = new GroveSpeechAdapter(client, onError);
    const utterance = speech.speak(`${"word ".repeat(1500)}`.trim());
    await flush();
    utterance.cancel();
    await flush();
    expect(utterance.status).toEqual({ type: "ended", reason: "cancelled" });
    expect(FakeAudio.instances[0]!.paused).toBe(true);
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    expect(revoked).toContain("blob:1");
    expect(onError).not.toHaveBeenCalled();
    // Cancelling the first chunk must not let the second one start playing.
    expect(FakeAudio.instances).toHaveLength(1);
  });

  it("cancelling while a request is in flight is not an error, though the abort rejects it", async () => {
    const { client, onError } = setup();
    client.synthesizeSpeech.mockImplementation(
      (_text: string, signal?: AbortSignal) =>
        new Promise<Blob>((_resolve, reject) => {
          signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const utterance = new GroveSpeechAdapter(client, onError).speak("hello");
    await flush();
    utterance.cancel();
    await flush();
    expect(utterance.status).toEqual({ type: "ended", reason: "cancelled" });
    expect(onError).not.toHaveBeenCalled();
  });

  it("cancelling during the LAST chunk stays cancelled instead of reading as finished", async () => {
    const { client, onError } = setup();
    const utterance = new GroveSpeechAdapter(client, onError).speak("one short answer");
    await flush();
    expect(utterance.status.type).toBe("running");
    utterance.cancel();
    await flush();
    expect(utterance.status).toEqual({ type: "ended", reason: "cancelled" });
  });

  it("a failed request ends as an error and reports it once", async () => {
    const { client, onError } = setup();
    client.synthesizeSpeech.mockRejectedValue(new Error("provider down"));
    const utterance = new GroveSpeechAdapter(client, onError).speak("hello");
    await flush();
    expect(utterance.status.type).toBe("ended");
    expect(utterance.status).toMatchObject({ reason: "error" });
    expect(onError).toHaveBeenCalledTimes(1);
  });
});
