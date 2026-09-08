import { describe, expect, it, vi } from "vitest";

import {
  acknowledgeComposerDraft,
  beginComposerDraftSubmission,
  flushComposerDraft,
  readComposerDraft,
  rejectComposerDraftSubmission,
  sameComposerDraft,
  writeComposerDraft,
  type ComposerDraft,
} from "@/lib/grove/runtime/draft";

const KEY = "workspace-1";
const DRAFT: ComposerDraft = {
  text: "Keep this work",
  attachments: [{ name: "notes.txt", contentType: "text/plain" }],
};

type StorageDouble = Pick<Storage, "getItem" | "setItem" | "removeItem">;

function memoryStorage(): StorageDouble {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
    removeItem: (key) => void values.delete(key),
  };
}

function withStorage<T>(store: StorageDouble, run: () => T): T {
  vi.stubGlobal("window", { sessionStorage: store });
  try {
    return run();
  } finally {
    vi.unstubAllGlobals();
  }
}

describe("composer draft storage", () => {
  it("clears an acknowledged draft when nothing changed while the send was in flight", () => {
    withStorage(memoryStorage(), () => {
      writeComposerDraft(KEY, DRAFT);
      beginComposerDraftSubmission(KEY);
      acknowledgeComposerDraft(KEY);
      expect(readComposerDraft(KEY)).toBeNull();
    });
  });

  it("preserves a newer edit typed while the acknowledged send was in flight", () => {
    withStorage(memoryStorage(), () => {
      writeComposerDraft(KEY, DRAFT);
      beginComposerDraftSubmission(KEY);
      // The reader kept typing after dispatch, before the 204 landed.
      writeComposerDraft(KEY, { ...DRAFT, text: "Newer work" });
      acknowledgeComposerDraft(KEY);
      expect(readComposerDraft(KEY)).toMatchObject({ text: "Newer work" });
    });
  });

  it("never lets an empty optimistic editor remove a retryable draft", () => {
    withStorage(memoryStorage(), () => {
      writeComposerDraft(KEY, DRAFT);
      writeComposerDraft(KEY, { text: "", attachments: [] });
      expect(readComposerDraft(KEY)).toMatchObject({ text: DRAFT.text });
    });
  });

  it("turns restored file metadata into a re-add reminder", () => {
    const store: StorageDouble = {
      getItem: () => JSON.stringify(DRAFT),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    };

    withStorage(store, () => {
      expect(readComposerDraft(KEY)?.attachments).toEqual([
        { name: "notes.txt", contentType: "text/plain", pendingReAdd: true },
      ]);
    });
  });

  it("treats unavailable storage as no draft, and every mutator as inert", () => {
    const store: StorageDouble = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
      removeItem: () => {
        throw new Error("denied");
      },
    };

    withStorage(store, () => {
      expect(readComposerDraft(KEY)).toBeNull();
      expect(() => writeComposerDraft(KEY, DRAFT)).not.toThrow();
      expect(() => beginComposerDraftSubmission(KEY)).not.toThrow();
      expect(() => acknowledgeComposerDraft(KEY)).not.toThrow();
      expect(() => flushComposerDraft(KEY, DRAFT)).not.toThrow();
    });
  });

  it("leaves the draft untouched after a rejected submission", () => {
    withStorage(memoryStorage(), () => {
      writeComposerDraft(KEY, DRAFT);
      beginComposerDraftSubmission(KEY);
      rejectComposerDraftSubmission(KEY);
      expect(readComposerDraft(KEY)).toMatchObject({ text: DRAFT.text });
    });
  });

  it("flushes immediately, bypassing the debounce, for a re-add dismissal", () => {
    withStorage(memoryStorage(), () => {
      flushComposerDraft(KEY, DRAFT);
      expect(readComposerDraft(KEY)).toMatchObject({ text: DRAFT.text });
      flushComposerDraft(KEY, { text: "", attachments: [] });
      expect(readComposerDraft(KEY)).toBeNull();
    });
  });

  it("recognizes a restored reminder when its unavailable MIME type becomes generic", () => {
    const reminder = { name: "notes.txt", contentType: "text/plain", pendingReAdd: true };
    expect(sameComposerDraft(
      { text: "", attachments: [reminder] },
      { text: "", attachments: [{ ...reminder, contentType: "application/octet-stream" }] },
    )).toBe(true);
  });

  it("compares attachment intent, including the reminder flag, as well as text", () => {
    expect(sameComposerDraft(DRAFT, DRAFT)).toBe(true);
    expect(
      sameComposerDraft(DRAFT, {
        ...DRAFT,
        attachments: [{ name: "other.txt", contentType: "text/plain" }],
      }),
    ).toBe(false);
    expect(
      sameComposerDraft(DRAFT, {
        ...DRAFT,
        attachments: [{ ...DRAFT.attachments[0]!, pendingReAdd: true }],
      }),
    ).toBe(false);
  });
});
