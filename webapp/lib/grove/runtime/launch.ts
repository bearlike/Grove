"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";

import { buildCreateRequest } from "@/lib/grove/adapters";
import type { CreateWorkspaceRequest, WorkspaceStateView } from "@/lib/grove/api";
import { useCreateWorkspace } from "@/lib/grove/hooks";
import { customModelError } from "@/lib/grove/adapters/launch";
import { useLaunchControls, type LaunchState } from "@/components/grove/launch/launch-state";

type CreateWorkspace = (request: CreateWorkspaceRequest) => Promise<WorkspaceStateView>;
type Navigate = (href: string) => void;
type RestorePrompt = (prompt: string) => void;

/** Submit a launch prompt through the single create request builder. */
export async function submitLaunch(
  state: LaunchState,
  prompt: string,
  createWorkspace: CreateWorkspace,
  navigate: Navigate,
  restorePrompt: RestorePrompt,
): Promise<void> {
  try {
    const workspace = await createWorkspace(buildCreateRequest(state, prompt));
    navigate(`/w/${workspace.id}`);
  } catch (error) {
    // The composer is cleared optimistically on send, so a refusal has to hand
    // the words back. An error beside an empty box is worse than no error: the
    // one thing the user would retry with is gone.
    restorePrompt(prompt);
    throw error;
  }
}

export interface LaunchSubmit {
  readonly prompt: string;
  readonly setPrompt: (prompt: string) => void;
  readonly submit: () => void;
  readonly canSubmit: boolean;
  readonly error: Error | null;
  readonly isPending: boolean;
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

  const submit = useCallback(() => {
    const text = prompt.trim();
    if (
      text === "" ||
      create.isPending ||
      (controls.values.customModel && customModelError(controls.values.model ?? "") !== null)
    ) {
      return;
    }
    setPrompt("");
    void submitLaunch(
      controls,
      text,
      create.mutateAsync,
      (href) => router.push(href),
      setPrompt,
    ).catch(() => {
      // Swallowed HERE and nowhere else: the mutation already holds this error
      // and the surface renders it from `create.error`. Rethrowing would only
      // reach an unhandled rejection.
    });
  }, [controls, create.isPending, create.mutateAsync, prompt, router]);

  return {
    prompt,
    setPrompt,
    submit,
    canSubmit: prompt.trim() !== "" && !create.isPending,
    error: create.error,
    isPending: create.isPending,
  };
}
