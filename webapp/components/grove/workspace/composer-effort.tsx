"use client";

import { useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { ModelSelector, type ModelOption } from "@/components/assistant-ui/model-selector";
import { EffortMark } from "@/components/grove/effort-icon";
import { effortLabel } from "@/lib/grove/adapters/launch";
import { useSessionControls, useSwitchEffort } from "@/lib/grove/hooks";
import { ControlTip } from "./composer-tip";

/**
 * The running session's reasoning-effort control, composed into the workspace
 * composer beside the model control.
 *
 * Levels come from `SessionControlsView.efforts`, which the engine fills only
 * for a session whose native control channel can carry the change, so a
 * terminal session or an agent with no ladder draws nothing rather than a
 * control that could only refuse.
 *
 * Nothing reports the ACTIVE effort back (Claude acknowledges any value and no
 * transcript record carries it), so the trigger names what this view last
 * requested and never claims what the provider is running. A request that was
 * delivered says so; it does not say "set".
 */
export function ComposerEffort({
  workspaceId,
}: {
  readonly workspaceId: string;
}): ReactNode {
  const [open, setOpen] = useState(false);
  const [requested, setRequested] = useState<string | null>(null);
  const controls = useSessionControls(workspaceId);
  const switchEffort = useSwitchEffort(workspaceId);
  const levels = controls.data?.efforts ?? [];
  // A delivery in flight disables every row rather than the control, so the
  // menu still reads while it cannot act.
  const options = useMemo<readonly ModelOption[]>(
    () =>
      levels.map((level) => ({
        id: level,
        name: effortLabel(level),
        icon: <EffortMark level={level} />,
        disabled: switchEffort.isPending,
      })),
    [levels, switchEffort.isPending],
  );

  if (levels.length === 0) return null;

  const stateNote = requested
    ? `Requested effort: ${effortLabel(requested)}`
    : "The agent has not been asked to change it";

  return (
    <ModelSelector.Root
      models={options}
      value={requested ?? ""}
      open={open}
      onOpenChange={setOpen}
      onValueChange={(level) => {
        switchEffort.mutate(level, {
          onSuccess: () => {
            setRequested(level);
            // Delivery is all the protocol acknowledges; no surface reports the
            // active level back, so the toast claims nothing about its effect.
            toast.success("Effort request delivered");
          },
          onError: (error) => {
            toast.error("Couldn’t deliver effort request", { description: error.message });
          },
        });
      }}
    >
      {/* The trigger appends its own chevron, so compose it directly rather than
          wrapping it in `asChild`, exactly as the model control does. */}
      <ControlTip tip={`Reasoning effort. ${stateNote}`} suppressed={open}>
        <ModelSelector.Trigger
          variant="outline"
          size="sm"
          aria-label="Reasoning effort"
          aria-description={stateNote}
          className="min-h-[24px] max-w-[160px]"
          data-testid="composer-effort-trigger"
        >
          {/* The resting glyph is the mark's own fallback, so it takes the mark's size. */}
          <EffortMark level={requested ?? ""} />
          <span className="truncate">{requested ? effortLabel(requested) : "Effort"}</span>
        </ModelSelector.Trigger>
      </ControlTip>
      <ModelSelector.Content
        align="start"
        data-testid="composer-effort-menu"
        aria-label="Choose reasoning effort"
      >
        {/* cmdk anchors list navigation on its input, so an unsearchable list
            still needs one: hidden, read-only and named after THIS control. */}
        <div className="sr-only">
          <ModelSelector.Search readOnly aria-label="Reasoning effort" />
        </div>
        <ModelSelector.List>
          <ModelSelector.Empty>No levels available.</ModelSelector.Empty>
          <ModelSelector.Group>
            {options.map((option) => (
              <ModelSelector.Item
                key={option.id}
                model={option}
                title={option.name}
                data-testid="composer-effort-item"
              />
            ))}
          </ModelSelector.Group>
        </ModelSelector.List>
      </ModelSelector.Content>
    </ModelSelector.Root>
  );
}
