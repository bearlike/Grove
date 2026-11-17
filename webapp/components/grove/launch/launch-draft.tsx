"use client";

import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import { usePathname } from "next/navigation";

import { LaunchStateProvider } from "./launch-state";
import { useLaunchDictation } from "./launch-dictation";
import { useLaunchSubmit, type LaunchSubmit } from "@/lib/grove/runtime/launch";

export interface LaunchDraft {
  readonly launch: LaunchSubmit;
  readonly dictation: ReturnType<typeof useLaunchDictation>;
}

const LaunchDraftContext = createContext<LaunchDraft | null>(null);

/**
 * The ONE creation draft: every control answer, the brief, the staged files
 * and the microphone, shared by the landing composer and the More options
 * dialog.
 *
 * It sits in the shell because the dialog does: it opens from the fleet as well
 * as from `/`, and the two surfaces have no common parent below the shell. A
 * draft per surface was what let More options open on a blank form beside a
 * written brief, with its own builder and its own idea of every default.
 *
 * Its lifetime is therefore longer than a route's, so it releases what must
 * not outlive one: a recording is cancelled on navigation, because a microphone
 * left live behind a page the reader has left is worse than a lost take. The
 * brief itself survives, which is the draft store's job anyway.
 */
export function LaunchDraftProvider({ children }: { readonly children: ReactNode }): ReactNode {
  return (
    <LaunchStateProvider>
      <LaunchDraftState>{children}</LaunchDraftState>
    </LaunchStateProvider>
  );
}

function LaunchDraftState({ children }: { readonly children: ReactNode }): ReactNode {
  const launch = useLaunchSubmit();
  const dictation = useLaunchDictation(launch.setPrompt, launch.submit);
  const pathname = usePathname();
  const route = useRef(pathname);
  const { cancel } = dictation;
  useEffect(() => {
    if (route.current === pathname) return;
    route.current = pathname;
    cancel();
  }, [cancel, pathname]);
  return (
    <LaunchDraftContext.Provider value={{ launch, dictation }}>{children}</LaunchDraftContext.Provider>
  );
}

export function useLaunchDraft(): LaunchDraft {
  const ctx = useContext(LaunchDraftContext);
  if (!ctx) throw new Error("useLaunchDraft must be used inside <LaunchDraftProvider>");
  return ctx;
}
