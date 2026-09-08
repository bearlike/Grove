"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";

import {
  attachmentCountError,
  attachmentError,
  buildCreateRequest,
  type StagedAttachment,
} from "@/lib/grove/adapters";
import type { CreateWorkspaceRequest, WorkspaceStateView } from "@/lib/grove/api";
import { base64FromBytes } from "@/lib/grove/api";
import { useCreateWorkspace } from "@/lib/grove/hooks";
import { customModelError } from "@/lib/grove/adapters/launch";
import { useLaunchControls, type LaunchState } from "@/components/grove/launch/launch-state";
import {
  acknowledgeComposerDraft,
  beginComposerDraftSubmission,
  flushComposerDraft,
  rejectComposerDraftSubmission,
  useComposerDraft,
  type ComposerDraft,
} from "./draft";

type CreateWorkspace = (request: CreateWorkspaceRequest) => Promise<WorkspaceStateView>;
type Navigate = (href: string) => void;
type RestorePrompt = (prompt: string) => void;
type RestoreDraft = (prompt: string, attachments: readonly StagedAttachment[]) => void;
type AcknowledgeDraft = () => void;
type RejectDraft = () => void;

/** Submit a launch prompt through the single create request builder. */
export async function submitLaunch(
  state: LaunchState,
  prompt: string,
  attachments: readonly StagedAttachment[],
  createWorkspace: CreateWorkspace,
  navigate: Navigate,
  restorePrompt: RestorePrompt,
  restoreDraft?: RestoreDraft,
  acknowledgeDraft?: AcknowledgeDraft,
  rejectDraft?: RejectDraft,
): Promise<void> {
  try {
    const workspace = await createWorkspace(buildCreateRequest(state, prompt, attachments));
    acknowledgeDraft?.();
    navigate(`/w/${workspace.id}`);
  } catch (error) {
    rejectDraft?.();
    // The composer is cleared optimistically on send, so a refusal has to hand
    // the words back. An error beside an empty box is worse than no error: the
    // one thing the user would retry with is gone.
    restorePrompt(prompt);
    restoreDraft?.(prompt, attachments);
    throw error;
  }
}

export interface LaunchSubmit {
  readonly prompt: string;
  readonly setPrompt: (prompt: string) => void;
  readonly submit: () => void;
  readonly canSubmit: boolean;
  readonly error: Error | null;
  /**
   * Why the composer would not take something, stated WITHOUT a create having
   * been attempted.
   *
   * Kept apart from `error` because the two are different sentences: a picker
   * refusal happens before any request exists, so leading it with "Couldn't
   * create workspace" would name a failure that never occurred. This also
   * closes a gap that predates attachments — every RangeError
   * `buildCreateRequest` throws was swallowed, leaving the send button looking
   * inert.
   */
  readonly refusal: string | null;
  readonly isPending: boolean;
  /** The staged files, in pick order — which is the order the engine lists them in. */
  readonly attachments: readonly StagedAttachment[];
  readonly pendingReAdd: readonly string[];
  readonly addFiles: (files: readonly File[]) => Promise<void>;
  readonly removeFile: (index: number) => void;
  /** Overwrite the staged file at `index` in place — an annotation save. */
  readonly replaceFile: (index: number, file: File) => Promise<void>;
}

/** Read one picked file into the encoding the create request carries. */
async function stageFile(file: File): Promise<StagedAttachment> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  return { name: file.name, size: file.size, content_base64: base64FromBytes(bytes), file };
}

function draftFromLaunch(
  text: string,
  attachments: readonly StagedAttachment[],
): ComposerDraft {
  return {
    text,
    attachments: attachments.map(({ name }) => ({
      name,
      contentType: "application/octet-stream",
    })),
  };
}

/**
 * The composer's send, with no assistant-ui runtime under it.
 *
 * There WAS one, and removing it fixed a bug worth recording: mounting
 * `AssistantRuntimeProvider` on this route silently broke **every** client-side
 * navigation away from it. A rail link would call `preventDefault`, Next would
 * fetch the target's RSC payload (200), and the transition would then never
 * commit — `history.pushState` was never reached, the main thread sat idle at
 * 60fps, and the URL never changed. Bisecting the tree pinned it to that
 * provider; nothing else on the page mattered.
 *
 * The runtime was only ever there because `ComposerPrimitive.Input` is the one
 * multi-line composer input assistant-ui vendors, and the elements composer's
 * own `ComposerInput` is a single-line `<input>`. A task brief is a paragraph,
 * so the surface needs a textarea — which is a dozen lines of plain React and
 * does not need a runtime, a message list, or a thread.
 */
export function useLaunchSubmit(): LaunchSubmit {
  const controls = useLaunchControls();
  const create = useCreateWorkspace();
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  // The staged files sit BESIDE the draft, for the draft's own reason: the
  // composer is moved between an inline mount and a dialog one, so anything
  // held inside it is destroyed the moment the writer expands the box.
  const [attachments, setAttachments] = useState<readonly StagedAttachment[]>([]);
  const [pendingReAdd, setPendingReAdd] = useState<readonly string[]>([]);
  const [refusal, setRefusal] = useState<string | null>(null);
  const draft = draftFromLaunch(prompt, attachments);
  const outstanding = pendingReAdd.filter(
    (name) => !attachments.some((attachment) => attachment.name === name),
  );
  const snapshot = {
    ...draft,
    attachments: [
      ...draft.attachments,
      ...outstanding.map((name) => ({
        name,
        contentType: "application/octet-stream",
        pendingReAdd: true,
      })),
    ],
  };

  useComposerDraft({
    storageKey: "launch",
    draft: snapshot,
    onRestore: (saved) => {
      setPrompt((current) => current || saved.text);
      setPendingReAdd((current) =>
        current.length > 0 ? current : saved.attachments.map(({ name }) => name),
      );
    },
    onAcknowledged: () => setPendingReAdd([]),
  });

  const addFiles = useCallback(
    async (files: readonly File[]) => {
      setRefusal(null);
      const tooMany = attachmentCountError(attachments.length + files.length);
      if (tooMany) {
        setRefusal(tooMany);
        return;
      }
      const staged: StagedAttachment[] = [];
      for (const file of files) {
        // Checked before the bytes are read, not after: base64 of a 40 MB file
        // is 53 MB of string built only to be thrown away.
        const refused = attachmentError(file);
        if (refused) {
          setRefusal(refused);
          continue;
        }
        staged.push(await stageFile(file));
      }
      if (staged.length > 0) {
        const nextAttachments = [...attachments, ...staged];
        const nextPendingReAdd = pendingReAdd.filter(
          (name) => !staged.some((attachment) => attachment.name === name),
        );
        setAttachments(nextAttachments);
        setPendingReAdd(nextPendingReAdd);
        // A re-added file dismisses its reminder immediately: without this, a
        // navigation inside the debounce window would restore a reminder for a
        // file that is once again genuinely staged.
        flushComposerDraft("launch", {
          text: prompt,
          attachments: [
            ...nextAttachments.map(({ name }) => ({
              name,
              contentType: "application/octet-stream",
            })),
            ...nextPendingReAdd.map((name) => ({
              name,
              contentType: "application/octet-stream",
              pendingReAdd: true,
            })),
          ],
        });
      }
    },
    [attachments, pendingReAdd, prompt],
  );

  const removeFile = useCallback((index: number) => {
    setAttachments((current) => current.filter((_, at) => at !== index));
  }, []);

  // In place, at the same index: the row the user annotated is the row that
  // changes, and the picker order the engine lists files in is preserved. The
  // size refusal applies again because a rasterized PNG can outgrow its source.
  const replaceFile = useCallback(async (index: number, file: File) => {
    const refused = attachmentError(file);
    if (refused) {
      setRefusal(refused);
      return;
    }
    const staged = await stageFile(file);
    setAttachments((current) => current.map((row, at) => (at === index ? staged : row)));
  }, []);

  const submit = useCallback(() => {
    const text = prompt.trim();
    if (
      text === "" ||
      create.isPending ||
      (controls.values.customModel && customModelError(controls.values.model ?? "") !== null)
    ) {
      return;
    }
    setRefusal(null);
    setPrompt("");
    setAttachments([]);
    beginComposerDraftSubmission("launch");
    void submitLaunch(
      controls,
      text,
      attachments,
      create.mutateAsync,
      (href) => router.push(href),
      setPrompt,
      (restoredText, restoredAttachments) => {
        setPrompt((current) => current || restoredText);
        setAttachments((current) => (current.length === 0 ? restoredAttachments : current));
        setPendingReAdd((current) =>
          current.filter(
            (name) => !restoredAttachments.some((attachment) => attachment.name === name),
          ),
        );
      },
      () => acknowledgeComposerDraft("launch"),
      () => rejectComposerDraftSubmission("launch"),
    ).catch((error: unknown) => {
      // A refusal from the BUILDER never reaches the mutation, so it has no
      // other way onto the screen. A mutation failure is already in
      // `create.error`; rethrowing either would only reach an unhandled
      // rejection.
      if (error instanceof RangeError) setRefusal(error.message);
    });
  }, [attachments, controls, create.isPending, create.mutateAsync, prompt, router]);

  return {
    prompt,
    setPrompt,
    submit,
    canSubmit: prompt.trim() !== "" && !create.isPending,
    error: create.error,
    refusal,
    isPending: create.isPending,
    attachments,
    pendingReAdd: outstanding,
    addFiles,
    removeFile,
    replaceFile,
  };
}
