"use client";

import type { ReactNode } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { runtimeGlyph } from "@/components/grove/fleet/tokens";
import { currentPreview } from "@/lib/grove/adapters/launch";
import { useLaunchDraft } from "./launch-draft";
import { LAUNCH_TESTIDS, useLaunchControls } from "./launch-state";

type NoteTone = "note" | "warning" | "caution";

/** `LaunchResolver._remote_branch_plan`'s reason, word for word. */
export const REMOTE_BRANCH_REASON = "A remote runtime works on its own branch.";

/** The engine's effective runtime and placement, stated as one quiet outcome. */
function launchLine(
  preview: ReturnType<typeof currentPreview>,
  values: ReturnType<typeof useLaunchControls>["values"],
): { readonly text: string; readonly runtime: "host" | "container" | "remote" } | null {
  const runtime: "host" | "container" | "remote" | null =
    preview?.runtime ??
    (values.runtime === "host" || values.runtime === "container"
      ? (values.runtime as "host" | "container")
      : values.runtime === null
        ? null
        : "remote");
  if (runtime === null) return null;
  const currentCheckout = preview?.placement === "root" || (preview === null && values.branchMode === "root");
  const placement = currentCheckout
    ? "current checkout"
    : `new branch from ${preview?.branch_plan?.kind === "auto" ? preview.branch_plan.base_ref : values.baseRef ?? "HEAD"}`;
  const place = runtime === "host" ? "this host" : runtime === "container" ? "a Dev Container" : preview?.remote_runtime ?? values.runtime;
  return { text: `Runs ${runtime === "container" ? "in" : "on"} ${place} · ${placement}`, runtime };
}

/** What the shelf's one note says, decided without hooks so a test can pin it. */
export interface NoteInput {
  /** The effective runtime and placement line, or `null` before it resolves. */
  readonly line: string | null;
  readonly error: string | null;
  readonly refusal: string | null;
  readonly adjustments: readonly { readonly field: string; readonly reason: string }[];
  /** The draft itself moved a current checkout to a new branch (launch-state RULE 2). */
  readonly placementSwitched: boolean;
  /** The profile shown before the runtime chose one, which Undo restores. */
  readonly profileUndo: string | null;
}

export interface NoteText {
  readonly tone: NoteTone;
  readonly message: string;
  readonly source: "error" | "refusal" | "adjustment" | "outcome";
  /** The profile Undo restores, or `null` when nothing on the line is undoable. */
  readonly undoProfile: string | null;
}

/**
 * Red beats amber beats grey, and the line never stacks.
 *
 * Every adjustment is named, because a pill that moved without a word is the
 * confusion this note removes. The draft's own placement switch sends an
 * explicit branch, so the engine has nothing to adjust and would say nothing;
 * it is stated here in the engine's words. Only the profile is undoable: on a
 * remote runtime the checkout a branch Undo would restore is a launch the
 * engine refuses.
 */
export function noteFor(input: NoteInput): NoteText {
  if (input.error !== null) {
    return { tone: "caution", message: `Couldn’t create workspace. ${input.error}`, source: "error", undoProfile: null };
  }
  if (input.refusal !== null) {
    return { tone: "caution", message: input.refusal, source: "refusal", undoProfile: null };
  }
  const local =
    input.placementSwitched && !input.adjustments.some((item) => item.field === "branch_plan")
      ? [{ field: "branch_plan", reason: REMOTE_BRANCH_REASON }]
      : [];
  const adjustments = [...input.adjustments, ...local];
  if (adjustments.length > 0) {
    const profile = adjustments.some((item) => item.field === "agent_name") ? input.profileUndo : null;
    return {
      tone: "warning",
      message: adjustments.map((item) => item.reason).join(" "),
      source: "adjustment",
      undoProfile: profile,
    };
  }
  return { tone: "note", message: input.line ?? "", source: "outcome", undoProfile: null };
}

/** The one compact consequence line below the shelf, with red > amber > neutral priority. */
export function LaunchSummary({
  testIds,
}: {
  readonly testIds?: { readonly error: string; readonly refusal: string };
}): ReactNode {
  const state = useLaunchControls();
  const { values, set } = state;
  const { launch } = useLaunchDraft();
  const preview = currentPreview(state);
  const line = launchLine(preview, values);
  const note = noteFor({
    line: line?.text ?? null,
    error: launch.error ? launch.error.message : null,
    refusal: launch.refusal,
    adjustments: preview?.adjustments ?? [],
    placementSwitched: state.runtimePlacementUndo != null,
    profileUndo: state.profileUndo ?? null,
  });
  const tone = note.tone;
  const message = note.message;
  const testId =
    note.source === "error"
      ? (testIds?.error ?? LAUNCH_TESTIDS.error)
      : note.source === "refusal"
        ? (testIds?.refusal ?? LAUNCH_TESTIDS.refusal)
        : "launch-summary";
  const undoable = note.undoProfile;
  const undo = undoable !== null ? () => set({ agentName: undoable }) : undefined;

  if (message === "") return null;
  return (
    <LaunchNote
      tone={tone}
      message={message}
      runtime={line?.runtime ?? preview?.runtime ?? "host"}
      testId={testId}
      undo={undo}
      undoField={undoable !== null ? "agent_name" : undefined}
    />
  );
}

/** The presentational seam for the shelf's one alert-shaped line. */
export function LaunchNote({
  tone,
  message,
  runtime,
  testId,
  undo,
  undoField,
}: {
  readonly tone: NoteTone;
  readonly message: string;
  readonly runtime: "host" | "container" | "remote";
  readonly testId: string;
  readonly undo?: () => void;
  readonly undoField?: string;
}): ReactNode {
  const Icon = runtimeGlyph(runtime);
  return (
    <Alert
      role="status"
      aria-live={tone === "note" ? "off" : "polite"}
      data-note={tone}
      data-testid={testId}
      className="launch-note mt-3"
    >
      <span data-runtime-kind={runtime}>
        <Icon aria-hidden />
      </span>
      <AlertDescription>
        <span>{message}</span>
        {undo ? (
          <Button variant="link" size="sm" onClick={undo} data-undo-field={undoField}>
            Undo
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
