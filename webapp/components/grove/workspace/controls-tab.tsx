"use client";

import {
  CheckIcon,
  CpuIcon,
  PlayIcon,
  ServerIcon,
  SparklesIcon,
  SquareSlashIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, EmptyStateGreeting } from "@/components/elements/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import type { SessionControlView, SessionControlsView } from "@/lib/grove/api";
import { useInvokeControl, useSessionControls, useSwitchModel } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";
import { CardGrid, SectionCard, CardScroll } from "@/components/grove/card";

/**
 * Both lists use the pane's width instead of a column of it, and neither knows
 * how wide the pane is.
 *
 * A model id is a short label, so the chips simply flow and wrap — a grid track
 * would stretch the word "opus" across a third of the panel. A command carries
 * a name and a sentence of detail, so it gets a real column, sized by `auto-
 * fill` from a minimum: the panel is ~380 px docked and full-window undocked,
 * and a fixed `grid-cols-2` is wrong at both ends.
 */
const MODEL_LIST = "flex flex-wrap gap-1.5";
const CONTROL_GRID = "grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-x-3";

/**
 * The session's input-control surface: the model catalog with a switch, plus
 * the enumerated slash commands, skills and configured MCP servers.
 *
 * Read-only enumeration is the core value; Run and Switch are thin best-effort
 * verbs over the daemon's `/controls/*` routes. An agent kind with no control
 * surface (a shell or remote agent) renders the quiet empty state rather than
 * empty chrome.
 */
export function ControlsTab({ workspaceId }: { workspaceId: string }) {
  const { data, isLoading } = useSessionControls(workspaceId);
  const invoke = useInvokeControl(workspaceId);
  const switchModel = useSwitchModel(workspaceId);

  if (isLoading && !data) {
    return (
      <CardGrid data-testid="controls-tab">
        <Skeleton className="h-32" />
        <Skeleton className="h-48" />
      </CardGrid>
    );
  }
  if (!data || !hasAnyControl(data)) {
    return (
      <CardGrid className="place-content-center justify-items-center" data-testid="controls-tab">
        <EmptyState>
          <EmptyStateGreeting>This agent exposes no session controls.</EmptyStateGreeting>
        </EmptyState>
      </CardGrid>
    );
  }

  // A refusal (501 capability_unavailable, or a 409) is expected rather than
  // exceptional here — surface the daemon's own message and move on.
  const failure = invoke.error ?? switchModel.error;

  return (
    <CardGrid className="@xl:grid-cols-2" data-testid="controls-tab">
      {data.models.length > 0 && (
        <SectionCard
          icon={<CpuIcon />}
          title="Model"
          description="The catalog this agent will accept a switch to."
          action={
            data.permission_mode ? (
              // Not a badge: the permission mode is how this session was
              // configured, not something it is currently doing. `muted` was
              // the giveaway — a pill drawn in the metadata colour is metadata
              // that has been given chrome it does not use.
              <span
                className="font-mono text-xs text-content-tertiary"
                title="The permission mode this session prompts with by default"
              >
                {data.permission_mode}
              </span>
            ) : undefined
          }
          className="@xl:col-span-2"
        >
          <div className={MODEL_LIST} data-testid="model-picker">
            {data.models.map((id) => (
              <ModelChip
                key={id}
                id={id}
                selected={id === data.current_model}
                pending={switchModel.isPending}
                onSelect={() => switchModel.mutate(id)}
              />
            ))}
          </div>
        </SectionCard>
      )}

      <ControlList
        icon={<SquareSlashIcon />}
        label="Commands"
        items={data.commands}
        pending={invoke.isPending}
        onRun={(name) => invoke.mutate(name)}
      />
      <ControlList
        icon={<SparklesIcon />}
        label="Skills"
        items={data.skills}
        pending={invoke.isPending}
        onRun={(name) => invoke.mutate(name)}
      />

      {data.mcp_servers.length > 0 && (
        <SectionCard
          icon={<ServerIcon />}
          title="MCP servers"
          description={`${data.mcp_servers.length} configured for this session`}
          className="@xl:col-span-2"
        >
          {/* A LIST OF NAMES, NOT A ROW OF STATES. A badge marks something
              about an object that could change; every server here is simply
              configured, and none of them is more or less configured than the
              next. Fifteen pills that all say the same nothing is fifteen
              things the eye has to reject before it can read one.

              Mono stays — a server name is an identifier you would retype into
              a config file. What goes is the chrome around it. The repeated
              glyph goes with the pills: the card's own header already carries
              one, and stamping it fifteen more times says nothing the heading
              has not. */}
          <ul className="flex flex-wrap gap-x-3 gap-y-1">
            {data.mcp_servers.map((server) => (
              <li key={server.name} className="font-mono text-xs text-content-tertiary">
                {server.name}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {failure && (
        <p role="status" className="text-xs text-destructive @xl:col-span-2">
          {failure.message}
        </p>
      )}
    </CardGrid>
  );
}

/**
 * One selectable model.
 *
 * A `Badge` around a button rather than the vendored `ModelPicker`: the daemon
 * offers bare model ids and nothing else, so the picker's four descriptive
 * columns — family, capabilities, context, price — were all empty, and it drew
 * a `max-w-sm` single column of blank metadata down the middle of a pane twice
 * that wide. A short label is a chip.
 */
function ModelChip({
  id,
  selected,
  pending,
  onSelect,
}: {
  id: string;
  selected: boolean;
  pending: boolean;
  onSelect: () => void;
}) {
  return (
    <Badge asChild variant={selected ? "secondary" : "outline"}>
      <button
        type="button"
        aria-pressed={selected}
        disabled={pending}
        onClick={onSelect}
        className={cn(
          "min-w-0 justify-start gap-1.5 font-mono disabled:opacity-60",
          !selected && "hover:bg-accent hover:text-accent-foreground",
        )}
        data-testid="model-chip"
      >
        <CheckIcon className={cn("shrink-0", !selected && "invisible")} aria-hidden />
        <span className="truncate">{id}</span>
      </button>
    </Badge>
  );
}

function ControlList({
  icon,
  label,
  items,
  pending,
  onRun,
}: {
  icon: ReactNode;
  label: string;
  items: readonly SessionControlView[];
  pending: boolean;
  onRun: (name: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <SectionCard icon={icon} title={label} description={`${items.length} available`} flush>
      <CardScroll className="p-1.5">
        <ul className={CONTROL_GRID}>
          {items.map((item) => (
            <li
              key={`${item.scope}:${item.name}`}
              className="flex items-center gap-1 px-1.5 py-1 hover:bg-muted/50"
            >
              {/* The name is what you came to find, its description is what
                  tells you whether it is the right one — primary over tertiary,
                  which is the rank a list of near-identical rows needs most. */}
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-xs text-content-primary">/{item.name}</p>
                {item.detail && (
                  <p className="truncate text-xs text-content-tertiary" title={item.detail}>
                    {item.detail}
                  </p>
                )}
              </div>
              <Button
                size="xs"
                variant="ghost"
                disabled={pending}
                onClick={() => onRun(item.name)}
                aria-label={`Run ${item.name}`}
              >
                <PlayIcon aria-hidden />
                Run
              </Button>
            </li>
          ))}
        </ul>
      </CardScroll>
    </SectionCard>
  );
}

function hasAnyControl(controls: SessionControlsView): boolean {
  return (
    controls.models.length > 0 ||
    controls.commands.length > 0 ||
    controls.skills.length > 0 ||
    controls.mcp_servers.length > 0 ||
    controls.permission_mode != null
  );
}
