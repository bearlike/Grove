import { describe, expect, it } from "vitest";

import {
  appendTranscript,
  chunkSpeechText,
  dictationFilename,
  recorderMimeType,
  SPEECH_CHUNK_LIMIT,
} from "@/lib/grove/adapters/speech";

const words = (text: string): string[] => text.split(/\s+/).filter(Boolean);

describe("chunkSpeechText", () => {
  it("returns nothing for blank text and one chunk for short text", () => {
    expect(chunkSpeechText("   \n ")).toEqual([]);
    expect(chunkSpeechText("Hello there.")).toEqual(["Hello there."]);
  });

  it("never exceeds the limit and loses no word, however the text is shaped", () => {
    const sentence = "The quick brown fox jumps over the lazy dog. ";
    const paragraph = sentence.repeat(40).trim();
    const text = Array.from({ length: 12 }, () => paragraph).join("\n\n");
    const chunks = chunkSpeechText(text);
    expect(chunks.length).toBeGreaterThan(1);
    for (const chunk of chunks) expect(chunk.length).toBeLessThanOrEqual(SPEECH_CHUNK_LIMIT);
    expect(words(chunks.join(" "))).toEqual(words(text));
  });

  it("prefers a paragraph boundary over a sentence boundary", () => {
    const first = "A".repeat(30) + ".";
    const second = "B".repeat(30) + ". " + "C".repeat(30) + ".";
    const chunks = chunkSpeechText(`${first}\n\n${second}`, 70);
    expect(chunks).toEqual([first, second]);
  });

  it("cuts on a sentence when one paragraph alone is too long", () => {
    const text = ["One two three.", "Four five six.", "Seven eight nine."].join(" ");
    expect(chunkSpeechText(text, 20)).toEqual(["One two three.", "Four five six.", "Seven eight nine."]);
  });

  it("cuts on a word before it ever cuts through one", () => {
    const chunks = chunkSpeechText("alpha beta gamma delta epsilon", 12);
    expect(chunks).toEqual(["alpha beta", "gamma delta", "epsilon"]);
  });

  it("hard-splits a single unbroken run, still losing nothing", () => {
    const run = "x".repeat(25);
    const chunks = chunkSpeechText(run, 10);
    expect(chunks.every((chunk) => chunk.length <= 10)).toBe(true);
    expect(chunks.join("")).toBe(run);
  });
});

describe("recorder container choice", () => {
  it("prefers webm/opus, then mp4, then the browser default", () => {
    expect(recorderMimeType(() => true)).toBe("audio/webm;codecs=opus");
    expect(recorderMimeType((type) => type === "audio/mp4")).toBe("audio/mp4");
    expect(recorderMimeType(() => false)).toBeUndefined();
  });

  it("names the upload for what was recorded, so the provider reads the right container", () => {
    expect(dictationFilename("audio/mp4")).toBe("speech.m4a");
    expect(dictationFilename("audio/webm;codecs=opus")).toBe("speech.webm");
    expect(dictationFilename("")).toBe("speech.webm");
  });
});

describe("appendTranscript", () => {
  it("separates with one space, never doubling one and never leading with one", () => {
    expect(appendTranscript("", "hello")).toBe("hello");
    expect(appendTranscript("fix the", "bug")).toBe("fix the bug");
    expect(appendTranscript("fix the ", "bug")).toBe("fix the bug");
    expect(appendTranscript("keep", "")).toBe("keep");
  });
});
