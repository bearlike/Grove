"use client";

import type { ReactNode } from "react";
import {
  CopyIcon,
  PlayIcon,
  ServerIcon,
  SparklesIcon,
  SquareSlashIcon,
  TerminalIcon,
} from "lucide-react";

import {
  CardGrid,
  CardRegion,
  CardScroll,
  SectionCard,
} from "@/components/grove/card";
import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { TerminalBlock } from "@/components/elements/terminal-block";
import { runtimeLabel } from "@/components/grove/fleet/tokens";
import { HelpLabel } from "./help-hint";
import { Button } from "@/components/ui/button";
import {
  EmptyState,
  EmptyStateGreeting,
} from "@/components/elements/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  SessionControlView,
  SessionControlsView,
  WorkspaceStateView,
} from "@/lib/grove/api";
import { useInvokeControl, useSessionControls } from "@/lib/grove/hooks";
import { LifecycleActions } from "./lifecycle-actions";
import { SendKeysCard } from "./send-keys";
import { ShareCard } from "./share-card";

const CONTROL_GRID =
  "grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-x-3";
const CONTROL_FOCUS =
  "min-h-[24px] border focus-visible:border-ring focus-visible:ring-ring/50";

/**
 * Workspace verbs and the agent-session controls that the daemon reports.
 *
 * The first three cards are workspace concerns and remain useful around agents
 * that expose no control protocol. Commands, skills, and MCP enumeration are
 * session concerns, so their empty state is deliberately separate.
 */
export function ControlsTab({
  state,
  onKilled,
  canInterrupt,
}: {
  state: WorkspaceStateView;
  onKilled: () => void;
  canInterrupt: boolean;
}) {
  const workspaceId = state.id;
  const { data, isLoading } = useSessionControls(workspaceId);
  const invoke = useInvokeControl(workspaceId);

  const workspaceCards = (
    <>
      <SessionCard state={state} onKilled={onKilled} />
      <SendKeysCard
        workspaceId={workspaceId}
        status={state.status}
        canInterrupt={canInterrupt}
        native={state.native}
      />
      <ShareCard workspaceId={workspaceId} />
    </>
  );

  if (isLoading && !data) {
    return (
      <CardGrid className="@xl:grid-cols-2" data-testid="controls-tab">
        {workspaceCards}
        <Skeleton className="h-32" />
        <Skeleton className="h-48" />
      </CardGrid>
    );
  }

  if (!data || !hasVisibleControl(data)) {
    return (
      <CardGrid className="@xl:grid-cols-2" data-testid="controls-tab">
        {workspaceCards}
        <div className="flex flex-1 items-center justify-center">
          <EmptyState>
            <EmptyStateGreeting>
              This agent exposes no session controls.
            </EmptyStateGreeting>
          </EmptyState>
        </div>
      </CardGrid>
    );
  }

  return (
    <CardGrid className="@xl:grid-cols-2" data-testid="controls-tab">
      {workspaceCards}
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
          <ul className="flex flex-wrap gap-x-3 gap-y-1">
            {data.mcp_servers.map((server) => (
              <li
                key={server.name}
                className="font-mono text-xs text-content-tertiary"
              >
                {server.name}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}
      {invoke.error && (
        <p role="status" className="text-xs text-destructive @xl:col-span-2">
          {invoke.error.message}
        </p>
      )}
    </CardGrid>
  );
}

/** Attach, lifecycle, and runtime are one workspace session, not three cards. */
function SessionCard({
  state,
  onKilled,
}: {
  state: WorkspaceStateView;
  onKilled: () => void;
}) {
  return (
    <SectionCard
      icon={<TerminalIcon />}
      title="Session"
      className="@xl:col-span-2"
      data-testid="session-card"
    >
      {/*
        THE COMMAND TAKES THE FULL MEASURE AND THE OTHER TWO SHARE THE ROW BELOW.
        Three equal columns gave a 32-character workspace id a 222px well and
        broke `grove attach <id>` over four lines, while `Lifecycle` (two
        buttons) and `Runtime` (one truncating line) each held the same 222px
        with nothing to put in it. Sizing by information rather than by count
        is the whole fix: the id is the one value here that must be read
        character for character, so it gets the width, and the pair that fits
        in half a row gets half a row.
      */}
      <div className="grid gap-2">
        <SessionRegion
          label="Attach"
          tooltip={
            state.native
              ? "Use this command on the host. A native session attaches read-only: the pane is the protocol event stream, and steering goes through Grove."
              : "Use this command on the host. Containerized workspaces enter their own tmux session."
          }
        >
          <AttachCommand workspaceId={state.id} />
        </SessionRegion>
        <div className="grid gap-2 @lg:grid-cols-2">
          <SessionRegion
            label="Lifecycle"
            tooltip={state.native
              ? "Recover the agent without deleting workspace files, or permanently remove the workspace. Native sessions cannot be paused."
              : "Pause, resume, respawn, or permanently remove this workspace."}
          >
            <LifecycleActions state={state} onKilled={onKilled} />
          </SessionRegion>
          <SessionRegion
            label="Runtime"
            tooltip="The environment, agent, and branch currently assigned to this workspace."
          >
            <span
              className="block min-w-0 truncate text-xs"
              title={`${runtimeLabel(state.runtime)} · ${state.agent_name} · ${state.branch}`}
            >
              {runtimeLabel(state.runtime)} · {state.agent_name} · {state.branch}
            </span>
          </SessionRegion>
        </div>
      </div>
    </SectionCard>
  );
}

/** The exact CLI handoff, with no current-session prerequisite. */
export function AttachCommand({ workspaceId }: { workspaceId: string }) {
  const command = `grove attach ${workspaceId}`;
  return (
    <div className="min-w-0" data-testid="attach-command-card">
      <div className="flex min-w-0 items-start gap-2">
        <TerminalBlock
          command="attach"
          lines={[command]}
          visibleCount={1}
          done
          // This is a command to copy, not execution evidence. Suppress the
          // vendor's exit-status header and output-log minimum height.
          //
          // `break-all` is deliberately NOT here any more: at a full-width
          // measure the id fits, and a mid-token break is what turned one
          // command into four lines the moment the well was narrow. It wraps
          // at whitespace like the rest of the transcript, and the row scrolls
          // rather than clips if a future id outgrows even this width — a
          // command a reader cannot see in full is a command they cannot
          // retype, which is the whole point of §3's mono rule.
          className="min-w-0 max-w-none flex-1 [&>div:first-child]:hidden [&>div:last-child]:min-h-0 [&>div:last-child]:overflow-x-auto [&>div:last-child]:py-2 [&>div:last-child]:whitespace-pre"
          data-testid="attach-command"
        />
        <TooltipIconButton
          variant="ghost"
          tooltip="Copy attach command"
          aria-label="Copy attach command"
          className="size-6 shrink-0"
          onClick={() => {
            void navigator.clipboard.writeText(command);
          }}
        >
          <CopyIcon aria-hidden />
        </TooltipIconButton>
      </div>
    </div>
  );
}

/** Session facts get distinct cells so their verbs do not read as one sentence. */
function SessionRegion({
  label,
  tooltip,
  children,
}: {
  label: string;
  tooltip: string;
  children: ReactNode;
}) {
  return (
    <CardRegion>
      <HelpLabel label={label} tooltip={tooltip} />
      {children}
    </CardRegion>
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
    <SectionCard
      icon={icon}
      title={label}
      description={`${items.length} available`}
      flush
    >
      <CardScroll className="p-1.5">
        <ul className={CONTROL_GRID}>
          {items.map((item) => (
            <li
              key={`${item.scope}:${item.name}`}
              className="flex items-center gap-1 px-1.5 py-1 hover:bg-muted/50"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-xs text-content-primary">
                  /{item.name}
                </p>
                {item.detail && (
                  <p
                    className="truncate text-xs text-content-tertiary"
                    title={item.detail}
                  >
                    {item.detail}
                  </p>
                )}
              </div>
              <Button
                size="xs"
                variant="ghost"
                className={CONTROL_FOCUS}
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

function hasVisibleControl(controls: SessionControlsView): boolean {
  return (
    controls.commands.length > 0 ||
    controls.skills.length > 0 ||
    controls.mcp_servers.length > 0
  );
}
