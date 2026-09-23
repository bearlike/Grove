"use client";

import Link from "next/link";
import { Suspense } from "react";

import {
  Composer,
  ComposerActions,
  ComposerBar,
  ComposerSend,
  ComposerToolbar,
} from "@/components/elements/composer";
import { ExpandedComposer } from "@/components/grove/composer";
import { useCloseAnnotationOnUnmount } from "@/components/grove/annotation";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { AppLogo } from "@/components/grove/app-logo";
import { useCreateWorkspaceUi } from "@/components/grove/fleet/create-store";
import { useFleetSnapshot } from "@/components/grove/fleet/use-fleet";
import {
  LaunchAttachFiles,
  LaunchAttachmentChips,
} from "@/components/grove/launch/composer-files";
import { LaunchPillGroup } from "@/components/grove/launch/control-pill";
import { LaunchControlRow } from "@/components/grove/launch/controls/control-row";
import { ModelPill } from "@/components/grove/launch/controls/model-pill";
import { LaunchDerivedTitle } from "@/components/grove/launch/derived-title";
import {
  LaunchStateProvider,
  LAUNCH_TESTIDS,
  useLaunchControls,
} from "@/components/grove/launch/launch-state";
import { customModelError } from "@/lib/grove/adapters/launch";
import { useOnboardingDemands, useOnboardingUi } from "@/components/grove/onboarding";
import { useLaunchSubmit, type LaunchSubmit } from "@/lib/grove/runtime/launch";

/** The blank task brief that starts a new Grove workspace. */
export function LaunchSurface(): React.ReactNode {
  return (
    <LaunchStateProvider>
      <Suspense>
        <LaunchSurfaceContent />
      </Suspense>
    </LaunchStateProvider>
  );
}

function LaunchSurfaceContent(): React.ReactNode {
  const { values } = useLaunchControls();
  const openCreate = useCreateWorkspaceUi((state) => state.openFor);
  const fleet = useFleetSnapshot();
  const noProjects = !fleet.isPending && !fleet.isError && fleet.data?.projects.length === 0;
  const launch = useLaunchSubmit();
  const openTour = useOnboardingUi((state) => state.setOpen);
  // The staged files die with this route, and so must any pane editing one.
  useCloseAnnotationOnUnmount();
  // The tour stages its sample image and writes its example briefs through
  // this page's own composer API; see `use-onboarding-demands.ts`.
  useOnboardingDemands(launch);

  return (
    <main
      className="flex min-h-0 flex-1 flex-col"
      data-testid={LAUNCH_TESTIDS.page}
      // Only the measure. The composer's surface is the vendored
      // `ComposerBar`'s, so this file has no opinion about its look.
      //
      // 52rem, not the transcript's 44rem: this surface carries a configuration
      // shelf under the bar where a reply composer carries none, and at 44rem
      // its four spelled-out values could only fit by hiding half of them
      // behind glyphs. It stays well under the 78rem reading measure — this is
      // a form, not prose.
      style={{ ["--thread-max-width" as string]: "52rem" }}
    >
      {/*
        `justify-center` centres the block in the content panel; the bottom
        padding is what lifts it off that centre. Two reasons it needs lifting:
        the panel's own 48px header is ABOVE this box, so a mathematically
        centred child already sits low, and the composer grows downward as the
        brief is typed — anchoring it slightly high is what keeps it from
        drifting down the screen while you write.
      */}
      <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col justify-center gap-6 px-4 pb-16 md:pb-24">
        {/*
          The welcome block is the field's own stacking context, so the texture
          sits behind the mark AND the greeting with no z-index anywhere: the
          field is the first child and out of flow, everything after it is in
          flow and therefore paints over it.
        */}
        <div className="relative flex flex-col items-center gap-3">
          {/*
            Texture, and nothing a screen reader could use — `aria-hidden`
            keeps a nameless region from being announced between the brand and
            the greeting, which is the one piece of noise a landing page cannot
            afford. The class is a HOOK: the hairlines, the radial fade and
            `pointer-events: none` all belong to the theme. What the caller
            owns is the measure, because the theme's mask is `closest-side` and
            has no size of its own — a field with no box renders nothing at
            all. It is deliberately wider than the text it sits behind so the
            fade lands on empty space rather than mid-sentence.
          */}
          <span aria-hidden className="launch-brand-field absolute -inset-x-24 -top-16 bottom-0" />
          {/*
            THE APP ICON, not the bare mark: this is the one place in the app
            where the logo stands alone above a blank page with nothing else
            identifying the product, which is exactly the job the tiled icon
            does on a dock and a home screen. `size-12` rather than the mark's
            `size-10` because the tile's own padding insets the wheel, so the
            two read at the same optical weight only when the tile is larger.
          */}
          <AppLogo className="relative size-12" data-testid={LAUNCH_TESTIDS.brand} />
          <div className="relative flex flex-col items-center gap-1 text-center">
            <h1
              className="text-content-primary text-2xl font-semibold"
              data-testid={LAUNCH_TESTIDS.headline}
            >
              Welcome back.
            </h1>
            {/*
              Two tiers rather than one line doing both jobs. A single heading
              carrying the greeting AND the question makes them compete for the
              one emphasis this page has; the question is what the composer
              below answers, so it takes the supporting tier and hands the
              emphasis to the editor.
            */}
            <p className="text-content-secondary text-base">
              What would you like to build and improve today?
            </p>
          </div>
        </div>
        {/*
          ONE PILL GROUP AROUND THE WHOLE COMPOSER, spanning the toolbar and the
          shelf. `LaunchPillGroup` holds a single `openKind`, and that single
          key IS the exclusion rule — opening the model menu closes the project
          menu. Two groups would give the two halves of one composer independent
          open states, so both menus would stand open and the second would cover
          the first. Nothing throws; the control underneath simply stops being
          clickable.
        */}
        <LaunchPillGroup>
          <ExpandedComposer
            title="Write the brief"
            description="Room for the whole thing. Every control keeps the choice you made."
            testId="launch"
            expandLabel="Expand"
          >
            {(expanded, inputRef, expandControl) => (
              <LaunchComposer
                disabled={noProjects}
                launch={launch}
                expanded={expanded}
                inputRef={inputRef}
                expandControl={expandControl}
              />
            )}
          </ExpandedComposer>
        </LaunchPillGroup>
        {launch.error ? (
          <p className="text-sm" data-testid={LAUNCH_TESTIDS.error}>
            <span className="text-destructive">Couldn’t create workspace. </span>
            <span className="text-content-secondary">{launch.error.message}</span>
          </p>
        ) : null}
        {/*
          A refusal gets the error slot's treatment and NOT its lead sentence:
          nothing was attempted, so "Couldn't create workspace" would name a
          failure that never happened.
        */}
        {launch.refusal ? (
          <p className="text-sm" data-testid={LAUNCH_TESTIDS.refusal}>
            <span className="text-destructive">{launch.refusal}</span>
          </p>
        ) : null}
        {noProjects ? (
          <div className="text-content-tertiary flex flex-col items-center gap-2 text-center text-sm">
            <span>No projects are registered.</span>
            <Button variant="outline" size="sm" asChild>
              <Link href="/fleet">Run grove config add-project</Link>
            </Button>
          </div>
        ) : null}
        <div data-testid={LAUNCH_TESTIDS.derivedTitle}>
          <LaunchDerivedTitle prompt={launch.prompt} />
        </div>
        <div
          className="flex items-center justify-center gap-1"
          data-testid={LAUNCH_TESTIDS.footerLinks}
        >
          {/*
            The full form, and the reason the shelf can stay short. Brief, base
            ref, skip-init and save-as-defaults have no pill and are not
            supposed to get one — a shelf that carries every knob is the
            nine-field modal again, wearing a different shape.
          */}
          <Button variant="ghost" size="sm" onClick={() => openCreate(values.repoRoot ?? "")}>
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

/**
 * The task brief, on the vendored `elements/composer` anatomy the workspace
 * reply uses too: staged files, the editor, then the toolbar with attach on the
 * left and model, expand and send on the right.
 *
 * BELOW the bar sits the configuration shelf — what the workspace will BE, not
 * what the message says. It is ghosted (no edge, the sunken fill, muted pills)
 * so the bar stays the one surface that reads as the place you write.
 *
 * The editor is the vendored `Textarea`, and this route mounts no assistant-ui
 * runtime: mounting `AssistantRuntimeProvider` here silently broke every
 * client-side navigation away from the page (see `useLaunchSubmit`). So the
 * vendored standalone parts are driven by plain React state instead of
 * `ComposerPrimitive`.
 */
function LaunchComposer({
  disabled,
  launch,
  expanded,
  inputRef,
  expandControl,
}: {
  readonly disabled: boolean;
  readonly launch: LaunchSubmit;
  readonly expanded: boolean;
  readonly inputRef: React.RefObject<HTMLTextAreaElement | null>;
  readonly expandControl: React.ReactNode;
}): React.ReactNode {
  const { values } = useLaunchControls();
  const customModelInvalid =
    values.customModel && customModelError(values.model ?? "") !== null;
  const blocked = !launch.canSubmit || customModelInvalid;

  return (
    <Composer
      className={expanded ? "flex min-h-0 max-w-none flex-1 flex-col gap-2" : "flex max-w-none flex-col gap-2"}
      data-testid={LAUNCH_TESTIDS.composer}
    >
      <ComposerBar className={expanded ? "min-h-0 flex-1" : undefined}>
        <LaunchAttachmentChips
          files={launch.attachments}
          pendingReAdd={launch.pendingReAdd}
          onRemove={launch.removeFile}
          onReplace={(index, file) => void launch.replaceFile(index, file)}
        />
        <Textarea
          ref={inputRef}
          value={launch.prompt}
          onChange={(event) => launch.setPrompt(event.target.value)}
          onPaste={(event) => {
            // Match ComposerPrimitive.Input's native file-paste path without
            // mounting a runtime on the landing page. Text-only paste is untouched.
            const files = Array.from(event.clipboardData.files);
            if (disabled || files.length === 0) return;
            event.preventDefault();
            void launch.addFiles(files);
          }}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line. `isComposing` guards an
            // IME candidate window, where Enter commits a character rather than
            // submitting the brief.
            if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
            event.preventDefault();
            launch.submit();
          }}
          placeholder="Describe the work you want to do..."
          // The theme drops the Textarea's own border and ring inside the bar,
          // which is the field. min-h-24/max-h-64 is the one range delta from
          // the reply composer: this box is the whole screen, not a line under
          // a conversation. Expanded it fills the dialog instead of capping.
          className={
            expanded
              ? "min-h-0 w-full flex-1 resize-none px-3"
              : "max-h-64 min-h-24 w-full resize-none px-3"
          }
          autoFocus
          disabled={disabled}
          enterKeyHint="send"
          aria-label="Task brief"
          data-testid={LAUNCH_TESTIDS.input}
        />
        <ComposerToolbar>
          <LaunchAttachFiles onAdd={launch.addFiles} disabled={disabled} />
          <ComposerActions className="min-w-0">
            <ModelPill />
            {expandControl}
            <ComposerSend
              streaming={false}
              aria-label={launch.isPending ? "Creating workspace" : "Send message"}
              aria-busy={launch.isPending}
              idle={blocked}
              disabled={blocked}
              onClick={launch.submit}
            />
          </ComposerActions>
        </ComposerToolbar>
      </ComposerBar>
      {/* A `ComposerToolbar` so its pills take the toolbar's ghost rule. */}
      <ComposerToolbar className="composer-shelf mx-3 px-1 py-0.5">
        <LaunchControlRow />
      </ComposerToolbar>
    </Composer>
  );
}
