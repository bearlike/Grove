"use client";

import Link from "next/link";
import { Suspense } from "react";
import { FolderPlusIcon } from "lucide-react";

import { ExpandedComposer } from "@/components/grove/composer";
import { useCloseAnnotationOnUnmount } from "@/components/grove/annotation";
import { Button } from "@/components/ui/button";
import { AppLogo } from "@/components/grove/app-logo";
import { useCreateWorkspaceUi } from "@/components/grove/fleet/create-store";
import { useFleetSnapshot } from "@/components/grove/fleet/use-fleet";
import { LaunchPillGroup } from "@/components/grove/launch/control-pill";
import { LaunchComposer } from "@/components/grove/launch/launch-composer";
import { useLaunchDraft } from "@/components/grove/launch/launch-draft";
import { LaunchSuggestions } from "@/components/grove/launch/launch-suggestions";
import { projectSelectionValues } from "@/components/grove/launch/controls/project-pill";
import { useAddProjectUi } from "@/components/grove/projects/add-project-store";
import { LAUNCH_TESTIDS, useLaunchControls } from "@/components/grove/launch/launch-state";
import { useOnboardingDemands, useOnboardingUi } from "@/components/grove/onboarding";

/** The blank task brief that starts a new Grove workspace. */
export function LaunchSurface(): React.ReactNode {
  return (
    <Suspense>
      <LaunchSurfaceContent />
    </Suspense>
  );
}

function LaunchSurfaceContent(): React.ReactNode {
  const { set } = useLaunchControls();
  const openWizard = useAddProjectUi((state) => state.openWizard);
  const moreOptions = useCreateWorkspaceUi((state) => state.open);
  const openMoreOptions = useCreateWorkspaceUi((state) => state.setOpen);
  const fleet = useFleetSnapshot();
  const noProjects = !fleet.isPending && !fleet.isError && fleet.data?.projects.length === 0;
  // The draft lives in the shell (`LaunchDraftProvider`): the expand dialog
  // and More options both MOVE this composer, and neither move may lose it.
  const { launch } = useLaunchDraft();
  const openTour = useOnboardingUi((state) => state.setOpen);
  useCloseAnnotationOnUnmount();
  useOnboardingDemands(launch);

  // Fills an EMPTY brief and focuses the editor; sending stays a separate act,
  // and a typed draft is never overwritten — the preset joins it on a new line.
  // The editor may sit in the page or the expanded dialog, so it is found by testid.
  const tryPreset = (prompt: string): void => {
    launch.setPrompt((current) => (current.trim() ? `${current.trimEnd()}\n\n${prompt}` : prompt));
    requestAnimationFrame(() =>
      document.querySelector<HTMLTextAreaElement>(`[data-testid="${LAUNCH_TESTIDS.input}"]`)?.focus(),
    );
  };

  return (
    <main
      className="launch-surface flex min-h-0 flex-1 flex-col overflow-y-auto"
      data-testid={LAUNCH_TESTIDS.page}
      // Only the measure; wider than a reply so the shelf's spelled-out values fit one row.
      style={{ ["--thread-max-width" as string]: "64rem" }}
    >
      <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col justify-center gap-6 px-4 py-8 md:pb-24">
        <div className="launch-greeting flex items-center justify-center gap-3">
          <AppLogo className="size-9" data-testid={LAUNCH_TESTIDS.brand} />
          <h1
            className="text-content-primary text-2xl font-medium"
            data-testid={LAUNCH_TESTIDS.headline}
          >
            What should we work on?
          </h1>
        </div>
        {/* One group spans toolbar and shelf: its single open key is the menu exclusion rule. */}
        <LaunchPillGroup>
          <ExpandedComposer
            title="Write the brief"
            description="Room for the whole thing. Every control keeps the choice you made."
            testId="launch"
            expandLabel="Expand"
          >
            {(expanded, inputRef, expandControl) =>
              // While More options is open it holds the one composer, so this
              // page renders none: one draft, one editor, one tab stop.
              moreOptions ? null : (
                <LaunchComposer
                  disabled={noProjects}
                  expanded={expanded}
                  inputRef={inputRef}
                  expandControl={expandControl}
                />
              )
            }
          </ExpandedComposer>
        </LaunchPillGroup>
        {noProjects ? (
          <div className="text-content-tertiary flex flex-col items-center gap-2 text-center text-sm">
            <span>No projects are registered.</span>
            <Button
              variant="default"
              size="sm"
              data-testid="launch-add-project"
              onClick={() =>
                openWizard({ onAdded: (added) => set(projectSelectionValues(added)) })
              }
            >
              <FolderPlusIcon className="size-[1em] shrink-0" aria-hidden />
              Add a project
            </Button>
          </div>
        ) : null}
        <LaunchSuggestions onTry={tryPreset} />
        <div
          className="flex items-center justify-center gap-1"
          data-testid={LAUNCH_TESTIDS.footerLinks}
        >
          <Button variant="ghost" size="sm" onClick={() => openMoreOptions(true)}
            data-testid="launch-more-options">
            More options
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/fleet">Go to fleet</Link>
          </Button>
          <Button variant="ghost" size="sm" onClick={() => openTour(true)} data-testid="launch-take-tour">
            Take the tour
          </Button>
        </div>
      </div>
    </main>
  );
}
