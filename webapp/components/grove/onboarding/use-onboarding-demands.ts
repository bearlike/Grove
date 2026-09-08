"use client";

import { useEffect } from "react";

import { useAnnotationUi } from "@/components/grove/annotation";
import { fileFromStaged } from "@/lib/grove/adapters/attachments";
import type { LaunchSubmit } from "@/lib/grove/runtime/launch";
import { useOnboardingUi, type OnboardingDemand } from "./onboarding-store";
import { DEMO_PROMPTS, SAMPLE_IMAGE_NAME, SAMPLE_IMAGE_URL } from "./steps";

const LANDING_KINDS = ["sample-image", "annotate", "prompt", "reset"] as const;

/**
 * The landing page's one subscription to the tour.
 *
 * A demand is consumed exactly once, in the order the tour posts it, against
 * the composer's own API — the same `addFiles`, `setPrompt` and annotation
 * store a user click reaches. Nothing here bypasses a refusal or a size cap:
 * the sample image goes through `addFiles` like any picked file.
 *
 * `reset` is the tour tidying up after itself: its own prompts are cleared so a
 * brief the tour wrote is never sent by accident, and only those — a brief the
 * user typed before starting the tour is theirs and stays. The staged sample
 * image and any marks on it stay too; a reader who just learned to annotate
 * may want to send exactly that.
 */
export function useOnboardingDemands(launch: LaunchSubmit): void {
  const demand = useOnboardingUi((state) => state.demand);
  const take = useOnboardingUi((state) => state.take);
  const openAnnotator = useAnnotationUi((state) => state.open);
  const closeAnnotator = useAnnotationUi((state) => state.close);

  useEffect(() => {
    const mine = take(LANDING_KINDS);
    if (!mine) return;
    switch (mine.kind) {
      case "sample-image": {
        if (launch.attachments.some((file) => file.name === SAMPLE_IMAGE_NAME)) return;
        void fetch(SAMPLE_IMAGE_URL)
          .then((response) => response.blob())
          .then((blob) => launch.addFiles([new File([blob], SAMPLE_IMAGE_NAME, { type: "image/webp" })]))
          .catch(() => undefined);
        return;
      }
      case "annotate": {
        const index = launch.attachments.findIndex((file) => file.name === SAMPLE_IMAGE_NAME);
        if (index < 0) return;
        const staged = launch.attachments[index]!;
        openAnnotator({
          file: fileFromStaged(staged),
          onSave: (annotated) => void launch.replaceFile(index, annotated),
        });
        return;
      }
      case "prompt": {
        closeAnnotator();
        launch.setPrompt(mine.text);
        return;
      }
      case "reset": {
        closeAnnotator();
        if (DEMO_PROMPTS.includes(launch.prompt)) launch.setPrompt("");
        return;
      }
    }
    // `demand` is read only to re-run when a new one lands; `take` decides.
  }, [closeAnnotator, demand, launch, openAnnotator, take]);
}

/**
 * The workspace page's subscriptions: the same one-demand-one-consumer shape,
 * split by owner. The page owns the pane and the work tab; the composer owns
 * its text and mounts later, under the thread's runtime. A demand nobody has
 * taken yet stays in the store until its owner mounts — which is what lets
 * "navigate to the workspace and pick a tab" be one step.
 */
export function useWorkspaceOnboardingDemands<K extends OnboardingDemand["kind"]>(
  kinds: readonly K[],
  apply: (demand: Extract<OnboardingDemand, { kind: K }>) => void,
): void {
  const demand = useOnboardingUi((state) => state.demand);
  const take = useOnboardingUi((state) => state.take);
  useEffect(() => {
    const mine = take(kinds);
    if (mine) apply(mine);
    // `demand` is read only to re-run when a new one lands; `take` decides.
  }, [apply, demand, kinds, take]);
}
