"use client";

import { useEffect, useRef } from "react";

/** A per-tab retryable composer snapshot. */
export type ComposerDraftAttachment = {
  name: string;
  contentType: string;
  pendingReAdd?: boolean;
};

export type ComposerDraft = {
  text: string;
  attachments: readonly ComposerDraftAttachment[];
};

type DraftChannel = {
  /** Bumped on every dispatch and every acknowledgement; a debounce timer
   * whose captured generation no longer matches skips its write, which is
   * what stops a timer scheduled before a 204 from reviving a cleared draft. */
  generation: number;
  /** The exact storage content read at the moment a send began. An
   * acknowledgement clears the entry only if storage STILL equals this —
   * i.e. nothing was typed or re-added while the request was in flight. */
  baseline: ComposerDraft | null;
  latest: (() => ComposerDraft) | null;
  clear: (() => void) | null;
};

const PREFIX = "grove-composer-draft:";
const DEBOUNCE_MS = 300;
const channels = new Map<string, DraftChannel>();

function keyFor(key: string): string {
  return `${PREFIX}${key}`;
}

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function channelFor(storageKey: string): DraftChannel {
  const existing = channels.get(storageKey);
  if (existing) return existing;
  const channel: DraftChannel = { generation: 0, baseline: null, latest: null, clear: null };
  channels.set(storageKey, channel);
  return channel;
}

function isDraft(value: unknown): value is ComposerDraft {
  return (
    !!value &&
    typeof value === "object" &&
    "text" in value &&
    typeof value.text === "string" &&
    "attachments" in value &&
    Array.isArray(value.attachments) &&
    value.attachments.every(
      (attachment) =>
        attachment &&
        typeof attachment === "object" &&
        "name" in attachment &&
        typeof attachment.name === "string" &&
        "contentType" in attachment &&
        typeof attachment.contentType === "string",
    )
  );
}

/** Reads a draft back without letting stale or user-edited storage break a composer. */
export function readComposerDraft(storageKey: string): ComposerDraft | null {
  const store = storage();
  if (!storageKey || !store) return null;
  try {
    const raw = store.getItem(keyFor(storageKey));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isDraft(parsed)) return null;
    // Local file bytes cannot survive navigation. Every restored file is a
    // reminder, never a phantom staged attachment.
    return {
      text: parsed.text,
      attachments: parsed.attachments.map(({ name, contentType }) => ({
        name,
        contentType,
        pendingReAdd: true,
      })),
    };
  } catch {
    return null;
  }
}

/** A blank editor is ambiguous during send, so only a nonempty draft writes. */
export function writeComposerDraft(storageKey: string, draft: ComposerDraft): void {
  const store = storage();
  if (!storageKey || !store || (!draft.text && draft.attachments.length === 0)) return;
  try {
    store.setItem(keyFor(storageKey), JSON.stringify(draft));
  } catch {
    // Private browsing may deny storage. Sending must still work.
  }
}

/** Returns whether two snapshots describe the same retryable intent, reminders included. */
export function sameComposerDraft(left: ComposerDraft, right: ComposerDraft): boolean {
  return (
    left.text === right.text &&
    left.attachments.length === right.attachments.length &&
    left.attachments.every(
      (attachment, index) =>
        attachment.name === right.attachments[index]?.name &&
        !!attachment.pendingReAdd === !!right.attachments[index]?.pendingReAdd,
    )
  );
}

/**
 * Marks a send in flight, capturing storage exactly as it stands.
 *
 * The captured baseline — not the wire message assistant-ui hands `onNew` — is
 * what an acknowledgement compares against, because the wire message only
 * names REAL attachments: a text-only send with an untouched reminder still
 * beside it would otherwise look, wire-side, like the reminder was never
 * there, and an unconditional clear would delete it.
 */
export function beginComposerDraftSubmission(storageKey: string): void {
  if (!storageKey) return;
  const channel = channelFor(storageKey);
  channel.generation += 1;
  channel.baseline = readComposerDraft(storageKey);
}

/** A refusal changes nothing: the draft the reader would retry with is
 * already what storage holds, and assistant-ui restores the editor itself. */
export function rejectComposerDraftSubmission(storageKey: string): void {
  if (storageKey) channelFor(storageKey).baseline = null;
}

/**
 * Clears a submission's draft, but only if nothing moved while it was in
 * flight — a newer edit, or a re-added file, has already overwritten storage
 * by the time this runs, and that overwrite is what must survive.
 */
export function acknowledgeComposerDraft(storageKey: string): void {
  if (!storageKey) return;
  const channel = channelFor(storageKey);
  const baseline = channel.baseline;
  channel.baseline = null;
  channel.generation += 1;
  // Storage may still hold the submitted draft while newer typing waits for
  // its debounce. Flush live intent before deciding whether the entry is stale.
  const live = channel.latest?.();
  if (live && (live.text || live.attachments.length > 0)) {
    const normalized = { ...live, attachments: live.attachments.map((item) => ({ ...item, pendingReAdd: true })) };
    const newerText = live.text !== "" && live.text !== baseline?.text;
    const newerFiles = live.attachments.length > 0 &&
      (!baseline || !sameComposerDraft({ ...normalized, text: baseline.text }, baseline));
    if (newerText || newerFiles) {
      writeComposerDraft(storageKey, live);
      return;
    }
  }
  const store = storage();
  if (!store) return;
  const current = readComposerDraft(storageKey);
  const matches = baseline === null ? current === null : current !== null &&
    (sameComposerDraft(current, baseline) ||
      (current.text === "" && sameComposerDraft({ ...current, text: baseline.text }, baseline)));
  if (matches) {
    // Retire the reminder state as well as storage, or the next render can
    // schedule a fresh writer after this generation was acknowledged.
    channel.clear?.();
    try {
      store.removeItem(keyFor(storageKey));
    } catch {
      // Storage failures never change delivery semantics.
    }
  }
}

/**
 * Persists and restores a composer through one tab's `sessionStorage` entry.
 *
 * Restore lands after mount: the server has no `sessionStorage`, and
 * restoring on the first client render would disagree with the markup the
 * server sent. Writes debounce because a draft changes a keystroke at a
 * time; each debounce captures the channel's generation at schedule time so
 * a write that would resurrect an already-acknowledged draft is skipped.
 */
export function useComposerDraft({
  draft,
  onRestore,
  onAcknowledged,
  storageKey,
}: {
  readonly draft: ComposerDraft;
  readonly onRestore: (draft: ComposerDraft) => void;
  readonly onAcknowledged: () => void;
  readonly storageKey: string;
}): void {
  const restored = useRef(false);
  const draftRef = useRef(draft);
  const restoreRef = useRef(onRestore);
  const acknowledgedRef = useRef(onAcknowledged);
  draftRef.current = draft;
  restoreRef.current = onRestore;
  acknowledgedRef.current = onAcknowledged;

  useEffect(() => {
    restored.current = false;
    if (!storageKey) return;
    const saved = readComposerDraft(storageKey);
    if (saved) restoreRef.current(saved);
    restored.current = true;
  }, [storageKey]);

  useEffect(() => {
    if (!storageKey) return;
    const channel = channelFor(storageKey);
    const latest = () => draftRef.current;
    channel.latest = latest;
    channel.clear = () => {
      draftRef.current = { text: "", attachments: [] };
      acknowledgedRef.current();
    };
    return () => {
      if (channel.latest === latest) {
        channel.latest = null;
        channel.clear = null;
      }
    };
  }, [storageKey]);

  useEffect(() => {
    if (!storageKey || !restored.current) return;
    const generation = channelFor(storageKey).generation;
    const timer = window.setTimeout(() => {
      if (channelFor(storageKey).generation === generation) {
        writeComposerDraft(storageKey, draftRef.current);
      }
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [draft, storageKey]);

  // A route transition can unmount before the debounce elapses.
  useEffect(() => {
    return () => writeComposerDraft(storageKey, draftRef.current);
  }, [storageKey]);
}

/**
 * Writes a draft immediately, bypassing the debounce.
 *
 * The one caller is dismissing a re-add reminder: the reader can navigate
 * away within the debounce window, and the reminder must not survive that
 * race just because 300ms had not yet elapsed.
 */
export function flushComposerDraft(storageKey: string, draft: ComposerDraft): void {
  if (!storageKey) return;
  channelFor(storageKey).generation += 1;
  if (draft.text || draft.attachments.length > 0) {
    writeComposerDraft(storageKey, draft);
    return;
  }
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(keyFor(storageKey));
  } catch {
    // Storage failures never change delivery semantics.
  }
}
