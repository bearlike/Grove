"use client";

import { Maximize2Icon } from "lucide-react";
import Link from "next/link";
import { useCallback, useRef, useState } from "react";

import {
  Composer,
  ComposerActions,
  ComposerBar,
  ComposerSend,
  ComposerToolbar,
} from "@/components/elements/composer";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { BrandMark } from "@/components/grove/brand-mark";
import { useCreateWorkspaceUi } from "@/components/grove/fleet/create-store";
import { useFleetSnapshot } from "@/components/grove/fleet/use-fleet";
import { LaunchControlRow } from "@/components/grove/launch/controls/control-row";
import { LaunchDerivedTitle } from "@/components/grove/launch/derived-title";
import {
  LaunchStateProvider,
  LAUNCH_TESTIDS,
  useLaunchControls,
} from "@/components/grove/launch/launch-state";
import { customModelError } from "@/lib/grove/adapters/launch";
import { useLaunchSubmit, type LaunchSubmit } from "@/lib/grove/runtime/launch";

/** The blank task brief that starts a new Grove workspace. */
export function LaunchSurface(): React.ReactNode {
  return (
    <LaunchStateProvider>
      <LaunchSurfaceContent />
    </LaunchStateProvider>
  );
}

function LaunchSurfaceContent(): React.ReactNode {
  const { values } = useLaunchControls();
  const openCreate = useCreateWorkspaceUi((state) => state.openFor);
  const fleet = useFleetSnapshot();
  const noProjects = !fleet.isPending && !fleet.isError && fleet.data?.projects.length === 0;
  const launch = useLaunchSubmit();
  // The composer is MOVED between two mount points, never rendered twice: a
  // second copy would mean two editors, two tab stops and two accessible
  // names for one draft. That is only safe because the draft and every control
  // value live above this component (`useLaunchSubmit`, `LaunchStateProvider`),
  // so the remount carries nothing with it.
  const [expanded, setExpanded] = useState(false);
  const inlineInput = useRef<HTMLTextAreaElement | null>(null);

  // Radix restores focus to the element that opened the dialog, and that
  // element is inside the composer we just unmounted — so its ref points at a
  // node that no longer exists and focus would fall to the body. Put it back on
  // the freshly remounted inline textarea instead.
  const restoreFocus = useCallback((event: Event) => {
    event.preventDefault();
    requestAnimationFrame(() => inlineInput.current?.focus());
  }, []);

  const composer = (
    <LaunchComposer
      disabled={noProjects}
      launch={launch}
      expanded={expanded}
      onExpand={() => setExpanded(true)}
      inputRef={inlineInput}
    />
  );

  return (
    <main
      className="flex min-h-0 flex-1 flex-col"
      data-testid={LAUNCH_TESTIDS.page}
      // Only the measure. The composer's own surface, radius and padding come
      // from the vendored `ComposerBar`, so re-declaring `--composer-*` here
      // would be this file having an opinion about a look it does not own.
      //
      // 52rem, not the transcript's 44rem: this surface carries six labelled
      // controls under the textarea where a reply composer carries none, and at
      // 44rem they could only fit by hiding half their values behind glyphs.
      // It stays well under the 78rem reading measure — this is a form, not
      // prose.
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
      <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col justify-center gap-4 px-4 pb-16 md:pb-24">
        <div
          className="flex items-center justify-center gap-2 text-sm font-medium"
          data-testid={LAUNCH_TESTIDS.brand}
        >
          <BrandMark className="size-6" />
          <span>Grove</span>
        </div>
        <h1
          className="text-content-primary text-center text-2xl font-semibold"
          data-testid={LAUNCH_TESTIDS.headline}
        >
          What would you like to work on?
        </h1>
        {expanded ? null : composer}
        <Dialog open={expanded} onOpenChange={setExpanded}>
          <DialogContent
            // Only layout is overridden at the call site; the vendored dialog
            // keeps its own surface, radius and close affordance. `dvh` rather
            // than `vh` so a mobile keyboard shrinks the box instead of pushing
            // the send button under it.
            className="flex h-[90dvh] max-h-[90dvh] w-[calc(100vw-2rem)] flex-col gap-4 sm:max-w-3xl"
            onCloseAutoFocus={restoreFocus}
            data-testid={LAUNCH_TESTIDS.expanded}
          >
            <DialogHeader>
              {/*
                Deliberately NOT "Task brief": that is the textarea's own
                accessible name, and Radix labels the dialog from this title, so
                reusing it gives one screen reader two different things called
                the same thing inside each other.
              */}
              <DialogTitle>Write the brief</DialogTitle>
              <DialogDescription>
                Room for the whole thing. Every control keeps the choice you made.
              </DialogDescription>
            </DialogHeader>
            {composer}
          </DialogContent>
        </Dialog>
        {launch.error ? (
          <p className="text-sm" data-testid={LAUNCH_TESTIDS.error}>
            <span className="text-destructive">Couldn’t create workspace. </span>
            <span className="text-content-secondary">{launch.error.message}</span>
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
          <Button variant="ghost" size="sm" onClick={() => openCreate(values.repoRoot ?? "")}>
            More options
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <Link href="/fleet">Go to fleet</Link>
          </Button>
        </div>
      </div>
    </main>
  );
}

/**
 * The task brief, built from the vendored `elements/composer` slots.
 *
 * Everything structural — the paper shell, the toolbar's `justify-between`
 * row, the action group, the send button's arrow↔stop swap — is imported
 * rather than restyled. The bar consumes the design system's raised-surface
 * tuple at the composition seam so its boundary holds in either theme.
 *
 * The textarea is the ONE hand-written element, and it is why this surface no
 * longer mounts an assistant-ui runtime. `ComposerInput` is a single-line
 * `<input>` and a task brief is a paragraph, so the previous answer was to
 * borrow `ComposerPrimitive.Input` — which dragged in `AssistantRuntimeProvider`
 * and silently broke EVERY client-side navigation away from this route (see
 * `useLaunchSubmit` for the measurement). A textarea costs a dozen lines; the
 * runtime cost a message list, a thread and a converter this surface never had
 * any use for, plus the bug.
 */
function LaunchComposer({
  disabled,
  launch,
  expanded,
  onExpand,
  inputRef,
}: {
  readonly disabled: boolean;
  readonly launch: LaunchSubmit;
  readonly expanded: boolean;
  readonly onExpand: () => void;
  readonly inputRef: React.RefObject<HTMLTextAreaElement | null>;
}): React.ReactNode {
  const { values } = useLaunchControls();
  const customModelInvalid =
    values.customModel && customModelError(values.model ?? "") !== null;

  return (
    <Composer
      className={expanded ? "flex min-h-0 max-w-none flex-1 flex-col" : "max-w-none"}
      data-testid={LAUNCH_TESTIDS.composer}
    >
      <ComposerBar
        className={
          expanded
            ? "bg-surface-raised border-surface-edge surface-raised flex min-h-0 flex-1 flex-col border"
            : "bg-surface-raised border-surface-edge surface-raised border"
        }
      >
        <textarea
          ref={inputRef}
          value={launch.prompt}
          onChange={(event) => launch.setPrompt(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter breaks the line. `isComposing` guards an
            // IME candidate window, where Enter commits a character rather than
            // submitting the brief.
            if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
            event.preventDefault();
            launch.submit();
          }}
          placeholder="Describe the work you want to do..."
          // min-h-24/max-h-64 is the one sizing delta from the vendored reply
          // composer: this box is the whole screen, not a line under a
          // conversation that already carries the context. Expanded, it fills
          // the dialog instead of capping — the whole point of expanding is
          // that the brief is longer than the cap.
          className={
            expanded
              ? "placeholder:text-foreground/35 min-h-0 w-full flex-1 resize-none bg-transparent px-3 text-[15px] outline-none"
              : "placeholder:text-foreground/35 max-h-64 min-h-24 w-full resize-none bg-transparent px-3 text-[15px] outline-none"
          }
          autoFocus
          disabled={disabled}
          enterKeyHint="send"
          aria-label="Task brief"
          data-testid={LAUNCH_TESTIDS.input}
        />
        {/*
          `items-end` rather than the vendored `items-center`: the control row
          wraps to two lines and Send must stay in the composer's bottom-right
          corner, where it is on every other surface. Centred against a
          two-line group it floats in the middle of the bar instead.
        */}
        <ComposerToolbar className="items-end gap-2">
          <LaunchControlRow />
          <ComposerActions className="shrink-0">
            {expanded ? null : (
              <TooltipIconButton
                tooltip="Expand"
                side="top"
                onClick={onExpand}
                disabled={disabled}
                data-testid={LAUNCH_TESTIDS.expand}
              >
                <Maximize2Icon />
              </TooltipIconButton>
            )}
            <ComposerSend
              streaming={launch.isPending}
              idle={launch.canSubmit && !customModelInvalid}
              disabled={!launch.canSubmit || customModelInvalid}
              onClick={launch.submit}
            />
          </ComposerActions>
        </ComposerToolbar>
      </ComposerBar>
    </Composer>
  );
}
