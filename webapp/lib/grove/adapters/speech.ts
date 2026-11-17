/**
 * Pure rules for dictation and read-aloud — nothing here touches the network
 * or the browser, so each is testable without `groveClient` or a microphone.
 */

/** The provider's own per-request limit for text-to-speech. */
export const SPEECH_CHUNK_LIMIT = 4096;

/** Recorder containers in preference order; the first the browser supports wins. */
const RECORDER_MIME_TYPES = ["audio/webm;codecs=opus", "audio/mp4"] as const;

/** The container to ask `MediaRecorder` for, or `undefined` to take the browser's default. */
export function recorderMimeType(isTypeSupported: (type: string) => boolean): string | undefined {
  return RECORDER_MIME_TYPES.find((type) => isTypeSupported(type));
}

/**
 * The upload's filename for what was actually recorded.
 *
 * The provider recognises the container by this extension, so it follows the
 * recorder's REAL type, never the one requested: a browser asked for webm that
 * hands back something else must not mislabel the bytes.
 */
export function dictationFilename(mimeType: string): string {
  return mimeType.includes("mp4") ? "speech.m4a" : "speech.webm";
}

/**
 * A transcript appended to a draft, separated the way assistant-ui's own
 * composer separates a final dictation result, so the workspace and landing
 * composers read identically.
 */
export function appendTranscript(draft: string, transcript: string): string {
  if (!transcript) return draft;
  return draft && !draft.endsWith(" ") ? `${draft} ${transcript}` : `${draft}${transcript}`;
}

/** One boundary level: how to cut text, and what to put back between the pieces. */
interface Boundary {
  readonly split: (text: string) => string[];
  readonly joiner: string;
}

/**
 * Coarse to fine. Each level is tried only on a piece the level above could not
 * fit, so a boundary is always preferred over a cut through the middle of one.
 */
const BOUNDARIES: readonly Boundary[] = [
  { split: (text) => text.split(/\n\s*\n/), joiner: "\n\n" },
  { split: (text) => text.split(/(?<=[.!?…])\s+/), joiner: " " },
  { split: (text) => text.split(/\s+/), joiner: " " },
];

function pack(text: string, limit: number, level: number): string[] {
  if (text.length <= limit) return [text];
  const boundary = BOUNDARIES[level];
  if (!boundary) {
    // A single unbroken run longer than the limit: the only cut left.
    return Array.from({ length: Math.ceil(text.length / limit) }, (_, index) =>
      text.slice(index * limit, (index + 1) * limit),
    );
  }
  const chunks: string[] = [];
  let current = "";
  for (const piece of boundary.split(text).filter((part) => part !== "")) {
    const joined = current ? `${current}${boundary.joiner}${piece}` : piece;
    if (joined.length <= limit) {
      current = joined;
      continue;
    }
    if (current) chunks.push(current);
    if (piece.length <= limit) {
      current = piece;
    } else {
      chunks.push(...pack(piece, limit, level + 1));
      current = "";
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

/**
 * Text split into pieces of at most `limit` characters for one request each.
 *
 * Cuts fall on paragraph, then sentence, then word boundaries. Whitespace
 * BETWEEN pieces may be normalised (it is silent in speech); no other
 * character is dropped or reordered.
 */
export function chunkSpeechText(text: string, limit: number = SPEECH_CHUNK_LIMIT): string[] {
  const trimmed = text.trim();
  return trimmed ? pack(trimmed, limit, 0) : [];
}

/**
 * The loudness of one analyser frame, 0 (silence) to 1 (full scale).
 *
 * `getByteTimeDomainData` centres silence on 128; the RMS of the offsets is
 * what a level meter shows, where a peak would flicker on every consonant.
 */
export function rmsLevel(frame: Uint8Array): number {
  if (frame.length === 0) return 0;
  let sum = 0;
  for (const sample of frame) {
    const offset = (sample - 128) / 128;
    sum += offset * offset;
  }
  return Math.min(1, Math.sqrt(sum / frame.length));
}

/**
 * The recording as a row of CHUNKS, oldest first — one bar each.
 *
 * A chunk is opened every `CHUNK_MS` by the sampler and holds the loudest
 * level measured while it was open, so each bar is "how loud was that
 * moment", growing while its moment is still being recorded. The strip holds
 * as many chunks as fit its width: it fills left to right and only then
 * slides, which is the reference's motion — a tape writing, not a scope.
 */
export class LevelHistory {
  private chunks: number[] = [];

  constructor(private capacity: number) {
    this.capacity = Math.max(1, capacity);
  }

  /** Begin the next chunk; the oldest falls off once the strip is full. */
  open(): void {
    this.chunks.push(0);
    if (this.chunks.length > this.capacity) this.chunks.shift();
  }

  /** Fold one measured level into the open chunk. Nothing before the first `open`. */
  sample(level: number): void {
    const last = this.chunks.length - 1;
    if (last < 0) return;
    this.chunks[last] = Math.max(this.chunks[last]!, level);
  }

  /** Keep the newest tail when the strip gets narrower. */
  resize(capacity: number): void {
    this.capacity = Math.max(1, capacity);
    if (this.chunks.length > this.capacity) this.chunks = this.chunks.slice(-this.capacity);
  }

  bars(): readonly number[] {
    return this.chunks;
  }
}
