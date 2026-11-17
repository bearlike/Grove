import { afterEach, describe, expect, it, vi } from "vitest";

import { GroveDictationAdapter } from "@/lib/grove/runtime/speech";
import { LevelHistory, rmsLevel } from "@/lib/grove/adapters/speech";

/**
 * The live waveform is a real meter on the microphone stream, never a
 * decoration: each bar is a level the session measured, the newest at the
 * right edge, and the whole strip slides left as audio arrives.
 */

class FakeRecorder {
  static isTypeSupported = (): boolean => false;
  state = "inactive";
  mimeType = "audio/webm";
  ondataavailable: unknown = null;
  onstop: (() => void) | null = null;
  constructor(readonly stream: unknown) {}
  start(): void {
    this.state = "recording";
  }
  stop(): void {
    this.state = "inactive";
    this.onstop?.();
  }
}

/** The one Web Audio surface the meter needs, with every call recorded. */
class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  state = "running";
  closed = false;
  readonly source = { connect: vi.fn() };
  constructor() {
    FakeAudioContext.instances.push(this);
  }
  createMediaStreamSource(): { connect: ReturnType<typeof vi.fn> } {
    return this.source;
  }
  createAnalyser(): { fftSize: number; getByteTimeDomainData: (data: Uint8Array) => void } {
    return {
      fftSize: 0,
      getByteTimeDomainData: (data: Uint8Array) => data.fill(128 + 64),
    };
  }
  close(): Promise<void> {
    this.closed = true;
    return Promise.resolve();
  }
}

function stubBrowser(options: { audio: boolean }): void {
  FakeAudioContext.instances = [];
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  vi.stubGlobal("navigator", {
    mediaDevices: {
      getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] })),
    },
  });
  if (options.audio) vi.stubGlobal("AudioContext", FakeAudioContext);
  else vi.stubGlobal("AudioContext", undefined);
}

const flush = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => vi.unstubAllGlobals());

function adapter(): GroveDictationAdapter {
  return new GroveDictationAdapter(
    { transcribeSpeech: vi.fn(async () => ({ text: "" })), synthesizeSpeech: vi.fn() },
    () => {},
  );
}

describe("the recording session's level meter", () => {
  it("reads a level off the microphone stream once the recording runs", async () => {
    stubBrowser({ audio: true });
    const session = adapter().listen();
    await flush();
    expect(session.status.type).toBe("running");
    // A constant +64 offset from silence (128) is an RMS of 0.5 of full scale.
    expect(session.level()).toBeCloseTo(0.5, 2);
    expect(FakeAudioContext.instances[0]!.source.connect).toHaveBeenCalledTimes(1);
  });

  it("is silent before the grant and silent, not broken, without Web Audio", async () => {
    stubBrowser({ audio: false });
    const session = adapter().listen();
    expect(session.level()).toBe(0);
    await flush();
    expect(session.status.type).toBe("running");
    expect(session.level()).toBe(0);
  });

  it("closes its audio context on cancel, so the meter cannot outlive the mic", async () => {
    stubBrowser({ audio: true });
    const live = adapter();
    const session = live.listen();
    await flush();
    live.cancel();
    expect(FakeAudioContext.instances[0]!.closed).toBe(true);
    expect(session.level()).toBe(0);
  });

  it("closes its audio context when the recording stops for upload", async () => {
    stubBrowser({ audio: true });
    const session = adapter().listen();
    await flush();
    await session.stop();
    expect(FakeAudioContext.instances[0]!.closed).toBe(true);
  });
});

describe("rmsLevel", () => {
  it("is 0 for silence and 1 for full scale, on the byte time-domain encoding", () => {
    expect(rmsLevel(new Uint8Array([128, 128, 128]))).toBe(0);
    expect(rmsLevel(new Uint8Array([0, 255, 0, 255]))).toBeCloseTo(1, 1);
    expect(rmsLevel(new Uint8Array())).toBe(0);
  });
});

describe("LevelHistory", () => {
  // One entry per CHUNK of recording. The open chunk is the last entry and
  // keeps its loudest moment so far; `open()` starts the next one. The strip
  // holds as many chunks as fit and slides left only once it is full.
  it("starts empty-handed: no chunk is drawn before the first opens", () => {
    const history = new LevelHistory(3);
    expect(history.bars()).toEqual([]);
  });

  it("holds the open chunk's PEAK, so a loud moment is not erased by the silence after it", () => {
    const history = new LevelHistory(3);
    history.open();
    history.sample(0.2);
    history.sample(0.9);
    history.sample(0.1);
    expect(history.bars()).toEqual([0.9]);
  });

  it("fills left to right, newest LAST, and only then slides", () => {
    const history = new LevelHistory(3);
    for (const level of [0.2, 0.5, 0.9]) {
      history.open();
      history.sample(level);
    }
    expect(history.bars()).toEqual([0.2, 0.5, 0.9]);
    history.open();
    history.sample(0.1);
    expect(history.bars()).toEqual([0.5, 0.9, 0.1]);
  });

  it("resizes to the strip without losing the recent tail", () => {
    const history = new LevelHistory(2);
    history.open();
    history.sample(0.3);
    history.open();
    history.sample(0.6);
    history.resize(4);
    expect(history.bars()).toEqual([0.3, 0.6]);
    history.resize(1);
    expect(history.bars()).toEqual([0.6]);
  });
});
