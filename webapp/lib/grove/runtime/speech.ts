"use client";

import { createContext, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { DictationAdapter, SpeechSynthesisAdapter } from "@assistant-ui/react";

import { base64FromBytes, type GroveClient } from "@/lib/grove/api";
import { groveClient } from "@/lib/grove/hooks/client";
import { chunkSpeechText, dictationFilename, recorderMimeType, rmsLevel } from "@/lib/grove/adapters/speech";
import { refusalNotice } from "./notice";

/**
 * Dictation and read-aloud, as assistant-ui's own two adapter interfaces.
 *
 * Both are thin: the daemon holds the provider key and does the work, and this
 * file only moves one recording up and one answer's audio down. Neither one is
 * live — a recording is uploaded ONCE, when the reader says they are done, so
 * changing their mind mid-sentence costs nothing and nothing is streamed to a
 * third party in the meantime.
 */

type SpeechClient = Pick<GroveClient, "transcribeSpeech" | "synthesizeSpeech">;

/**
 * One recording, from the microphone grant to the transcript.
 *
 * The status stays `running` until the transcript has been EMITTED. assistant-ui
 * polls `status` every 100 ms and tears the session down — unsubscribing
 * `onSpeech` — the moment it reads `ended`, so an `ended` that arrived before
 * the (slow) upload returned would silently drop the text the reader waited for.
 */
export class RecordingSession implements DictationAdapter.Session {
  status: DictationAdapter.Status = { type: "starting" };

  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  // The live level meter: an analyser on the SAME stream the recorder reads,
  // so the waveform never costs a second microphone grant.
  private audio: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private frame: Uint8Array<ArrayBuffer> | null = null;
  private readonly chunks: Blob[] = [];
  private readonly started: Promise<void>;
  private stopping: Promise<void> | undefined;
  private readonly speechListeners = new Set<(result: DictationAdapter.Result) => void>();
  private readonly startListeners = new Set<() => void>();
  private readonly endListeners = new Set<(result: DictationAdapter.Result) => void>();

  constructor(
    private readonly client: SpeechClient,
    private readonly onError: (error: unknown) => void,
  ) {
    this.started = this.begin();
  }

  private get isEnded(): boolean {
    return this.status.type === "ended";
  }

  /** Idempotent, so a double-pressed Done is still exactly one upload. */
  stop(): Promise<void> {
    this.stopping ??= this.finish();
    return this.stopping;
  }

  /** Discards the recording. No request is made, ever, for a cancelled session. */
  cancel(): void {
    if (this.isEnded) return;
    this.terminate({ type: "ended", reason: "cancelled" });
  }

  /**
   * The microphone's loudness right now, 0–1. Silence before the grant and
   * after any exit, and silence wherever Web Audio is unavailable — the
   * recording itself never depends on the meter.
   */
  level(): number {
    if (!this.analyser || !this.frame) return 0;
    this.analyser.getByteTimeDomainData(this.frame);
    return rmsLevel(this.frame);
  }

  onSpeechStart(callback: () => void): () => void {
    this.startListeners.add(callback);
    return () => void this.startListeners.delete(callback);
  }

  onSpeech(callback: (result: DictationAdapter.Result) => void): () => void {
    this.speechListeners.add(callback);
    return () => void this.speechListeners.delete(callback);
  }

  onSpeechEnd(callback: (result: DictationAdapter.Result) => void): () => void {
    this.endListeners.add(callback);
    return () => void this.endListeners.delete(callback);
  }

  private async begin(): Promise<void> {
    // Yield first: a failure on the very first line (no `mediaDevices` outside a
    // secure context) must land AFTER the caller has attached its listeners.
    await Promise.resolve();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // Cancelled (or failed) while the permission prompt was open: the grant
      // arrives for a session nobody is waiting on, so let the mic go at once.
      if (this.isEnded) return releaseTracks(stream);
      this.stream = stream;
      const mimeType = recorderMimeType((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) this.chunks.push(event.data);
      };
      recorder.start();
      this.recorder = recorder;
      this.meter(stream);
      this.status = { type: "running" };
      for (const listener of this.startListeners) listener();
    } catch (error) {
      this.fail(error);
    }
  }

  /** Best effort: a browser without `AudioContext` records fine and meters nothing. */
  private meter(stream: MediaStream): void {
    const Context = globalThis.AudioContext;
    if (typeof Context !== "function") return;
    try {
      const audio = new Context();
      const analyser = audio.createAnalyser();
      analyser.fftSize = 512;
      audio.createMediaStreamSource(stream).connect(analyser);
      this.audio = audio;
      this.analyser = analyser;
      this.frame = new Uint8Array(analyser.fftSize);
    } catch {
      this.audio = null;
    }
  }

  private async finish(): Promise<void> {
    await this.started;
    const recorder = this.recorder;
    if (this.isEnded || !recorder) return;
    try {
      const blob = await new Promise<Blob>((resolve) => {
        recorder.onstop = () => resolve(new Blob(this.chunks, { type: recorder.mimeType }));
        recorder.stop();
      });
      // The recording is complete; the mic has no further job.
      this.releaseMedia();
      const bytes = new Uint8Array(await blob.arrayBuffer());
      const { text } = await this.client.transcribeSpeech(
        base64FromBytes(bytes),
        dictationFilename(recorder.mimeType || blob.type),
      );
      // Cancelled during the upload: the reader no longer wants the words.
      if (this.isEnded) return;
      for (const listener of this.speechListeners) listener({ transcript: text, isFinal: true });
      this.terminate({ type: "ended", reason: "stopped" }, text);
    } catch (error) {
      this.fail(error);
    }
  }

  private fail(error: unknown): void {
    if (this.isEnded) return;
    this.terminate({ type: "ended", reason: "error" });
    this.onError(error);
  }

  /** The one exit: free the mic, publish the terminal status, then tell listeners. */
  private terminate(status: DictationAdapter.Status, transcript = ""): void {
    this.releaseMedia();
    this.status = status;
    for (const listener of this.endListeners) listener({ transcript, isFinal: true });
  }

  private releaseMedia(): void {
    const { recorder, stream, audio } = this;
    this.recorder = null;
    this.stream = null;
    this.audio = null;
    this.analyser = null;
    this.frame = null;
    void audio?.close().catch(() => undefined);
    if (recorder) {
      // A recorder that is still running would deliver a final chunk into a
      // session that has already been discarded.
      recorder.ondataavailable = null;
      if (recorder.state !== "inactive") recorder.stop();
    }
    if (stream) releaseTracks(stream);
  }
}

function releaseTracks(stream: MediaStream): void {
  for (const track of stream.getTracks()) track.stop();
}

/**
 * Speech-to-text for a composer.
 *
 * `cancel()` lives on the adapter because assistant-ui's composer runtime
 * exposes `startDictation`/`stopDictation` and nothing else: the session is
 * private to it, so Grove's Cancel button reaches the active one through the
 * object it supplied. After a cancel the session reads `ended`, and the core's
 * own status poll does the cleanup.
 */
export class GroveDictationAdapter implements DictationAdapter {
  private active: RecordingSession | null = null;
  // The live waveform reads this, so the session is typed as Grove's own
  // (with `level()`), which assistant-ui's `Session` does not know about.
  private readonly levelListeners = new Set<() => void>();

  constructor(
    private readonly client: SpeechClient,
    private readonly onError: (error: unknown) => void,
  ) {}

  listen(): RecordingSession {
    this.active?.cancel();
    this.active = new RecordingSession(this.client, this.onError);
    for (const listener of this.levelListeners) listener();
    return this.active;
  }

  cancel(): void {
    this.active?.cancel();
  }

  /** The active recording's loudness right now, 0–1; 0 with none active. */
  level(): number {
    return this.active?.level() ?? 0;
  }

  /** Called whenever a new recording starts, so a meter can restart its history. */
  onListen(callback: () => void): () => void {
    this.levelListeners.add(callback);
    return () => void this.levelListeners.delete(callback);
  }
}

export interface SpeechAdapters {
  /** What `useExternalStoreRuntime` takes: `dictation` and `speech` on its adapters. */
  readonly adapters: { dictation: DictationAdapter; speech: SpeechSynthesisAdapter };
  /** The same dictation adapter, for Grove's Cancel button. */
  readonly dictation: GroveDictationAdapter;
}

/**
 * Both adapters, or `undefined` while the daemon has no provider key.
 *
 * `available` is `whoami.speech`: without a key neither control may be offered,
 * because every click would end in a 409. Memoised so the runtime's identity
 * comparison of adapters sees a stable object.
 */
export function useSpeechAdapters(
  available: boolean,
  onNotice: (message: string) => void,
): SpeechAdapters | undefined {
  return useMemo(() => {
    if (!available) return undefined;
    const dictation = new GroveDictationAdapter(groveClient, (error) => onNotice(refusalNotice(error, "dictate")));
    const synthesis = new GroveSpeechAdapter(groveClient, (error) => onNotice(refusalNotice(error, "speak")));
    return { dictation, adapters: { dictation, speech: synthesis } };
  }, [available, onNotice]);
}

/** Gives Grove's own Cancel button the adapter; provided around the thread. */
export const DictationContext = createContext<GroveDictationAdapter | null>(null);

export type DictationPhase = "idle" | "recording" | "transcribing";

export interface DictationControls {
  readonly phase: DictationPhase;
  readonly start: () => void;
  /**
   * Stop recording and upload it; the transcript arrives through
   * `onTranscript` BEFORE the returned promise resolves, so a caller that
   * wants to act on the words (send them) can await this.
   */
  readonly done: () => Promise<void>;
  readonly cancel: () => void;
  /** The microphone's loudness right now, for the live waveform. */
  readonly level: () => number;
}

/**
 * Dictation driven directly, for the surface that mounts no assistant-ui runtime.
 *
 * The landing composer cannot use `ComposerPrimitive.Dictate` (every primitive
 * reads the runtime it deliberately omits), so it drives the SAME adapter by
 * hand — which is also why the state machine here mirrors what the core does
 * for the workspace composer: a session ends on stop, cancel or error alike.
 */
export function useDictationControls(
  onTranscript: (transcript: string) => void,
  onError: (message: string) => void,
): DictationControls {
  const [phase, setPhase] = useState<DictationPhase>("idle");
  // Read at call time, so a transcript lands on the draft as it stands THEN,
  // not as it stood when recording began.
  const latest = useRef({ onTranscript, onError });
  useEffect(() => {
    latest.current = { onTranscript, onError };
  });
  const adapter = useMemo(
    () => new GroveDictationAdapter(groveClient, (error) => latest.current.onError(refusalNotice(error, "dictate"))),
    [],
  );
  // A recording left running by a route change would hold the microphone open.
  useEffect(() => () => adapter.cancel(), [adapter]);

  const session = useRef<DictationAdapter.Session | null>(null);
  const start = useCallback(() => {
    const next = adapter.listen();
    session.current = next;
    next.onSpeech(({ transcript }) => latest.current.onTranscript(transcript));
    next.onSpeechEnd(() => setPhase("idle"));
    setPhase("recording");
  }, [adapter]);
  const done = useCallback(async () => {
    setPhase("transcribing");
    await session.current?.stop();
  }, []);
  const cancel = useCallback(() => adapter.cancel(), [adapter]);
  const level = useCallback(() => adapter.level(), [adapter]);
  return { phase, start, done, cancel, level };
}

/**
 * One answer being read aloud: chunks fetched one ahead of the one playing.
 *
 * The next chunk's request is issued BEFORE the current one plays, so a long
 * answer has no audible gap between its 4096-character requests.
 */
class SpokenAnswer implements SpeechSynthesisAdapter.Utterance {
  status: SpeechSynthesisAdapter.Status = { type: "starting" };

  private readonly abort = new AbortController();
  private readonly subscribers = new Set<() => void>();
  private audio: HTMLAudioElement | null = null;
  private stopPlayback: (() => void) | null = null;

  constructor(
    text: string,
    private readonly client: SpeechClient,
    private readonly onError: (error: unknown) => void,
  ) {
    void this.run(chunkSpeechText(text));
  }

  private get isEnded(): boolean {
    return this.status.type === "ended";
  }

  cancel = (): void => {
    if (this.isEnded) return;
    this.abort.abort();
    this.stopPlayback?.();
    this.end({ type: "ended", reason: "cancelled" });
  };

  subscribe = (callback: () => void): (() => void) => {
    this.subscribers.add(callback);
    return () => void this.subscribers.delete(callback);
  };

  private async run(chunks: readonly string[]): Promise<void> {
    try {
      let next = chunks.length > 0 ? this.fetchChunk(chunks[0]!) : null;
      for (let index = 0; next; index += 1) {
        const blob = await next;
        if (this.isEnded) return;
        const following = chunks[index + 1];
        next = following === undefined ? null : this.fetchChunk(following);
        await this.play(blob);
        if (this.isEnded) return;
      }
      this.end({ type: "ended", reason: "finished" });
    } catch (error) {
      // A cancel aborts the in-flight request; that rejection is not a failure.
      if (this.isEnded) return;
      this.end({ type: "ended", reason: "error", error });
      this.onError(error);
    }
  }

  private fetchChunk(text: string): Promise<Blob> {
    const request = this.client.synthesizeSpeech(text, this.abort.signal);
    // A prefetch that fails while an earlier chunk is still playing is awaited
    // later; without this it would be reported as unhandled in the meantime.
    request.catch(() => {});
    return request;
  }

  private play(blob: Blob): Promise<void> {
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    this.audio = audio;
    return new Promise<void>((resolve, reject) => {
      const settle = (outcome: () => void): void => {
        audio.onended = null;
        audio.onerror = null;
        audio.pause();
        URL.revokeObjectURL(url);
        this.audio = null;
        this.stopPlayback = null;
        outcome();
      };
      this.stopPlayback = () => settle(() => resolve());
      audio.onended = () => settle(() => resolve());
      audio.onerror = () => settle(() => reject(new Error("The audio could not be played.")));
      audio.play().then(
        () => this.running(),
        (error: unknown) => settle(() => reject(error)),
      );
    });
  }

  private running(): void {
    if (this.status.type !== "starting") return;
    this.status = { type: "running" };
    this.notify();
  }

  private end(status: SpeechSynthesisAdapter.Status): void {
    this.status = status;
    this.notify();
  }

  private notify(): void {
    for (const subscriber of this.subscribers) subscriber();
  }
}

/** Text-to-speech for an assistant answer, through the daemon's provider. */
export class GroveSpeechAdapter implements SpeechSynthesisAdapter {
  constructor(
    private readonly client: SpeechClient,
    private readonly onError: (error: unknown) => void,
  ) {}

  speak(text: string): SpeechSynthesisAdapter.Utterance {
    return new SpokenAnswer(text, this.client, this.onError);
  }
}
