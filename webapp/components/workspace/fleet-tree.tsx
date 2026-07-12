"use client";

import { useState } from "react";
import { ChevronDownIcon, MessagesSquare, Users, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Response } from "@/components/ai-elements/response";
import { ToolGroup } from "@/components/ai-elements/tool";
import { FileEditCard } from "@/components/chat/file-edit-view";
import { QuestionCard } from "@/components/workspace/question-card";
import { AgentStateMark } from "@/components/shared/state-mark";
import { RoleLabel } from "@/components/shared/role-label";
import { Stat } from "@/components/shared/stat";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import { chatItemsFromTurns, type ChatItem } from "@/lib/grove/chat-turns";
import { fleetMemberCount, type FleetNode } from "@/lib/grove/fleet";
import { useSessionTurns } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";

/**
 * The work panel's fleet browser (epic #170 north star, #174): itemizes a
 * session's in-session sub-agent fleet (#173's `parent_session_id` link)
 * as an expandable tree, one root per top-level session that spawned
 * sub-agents. A root with no children renders nothing (the flat
 * `active_subagents` count line stays the fallback for that case, see
 * `WorkPanel`'s `InfoTab`) — this component's own "no empty tree chrome"
 * gate is `fleetMemberCount(roots) === 0` returning `null` outright.
 *
 * Reuses the transcript's own leaf renderers VERBATIM (`ToolGroup`,
 * `FileEditCard`, `QuestionCard`, `Response`, `RoleLabel`) rather than the
 * `assistant-ui` thread shell — a completed sub-agent has nothing to steer,
 * so there is no composer/interrupt to wire, only the same read-only parts
 * the live chat panel already draws from `chatItemsFromTurns`.
 *
 * Test seams: `fleet-tree`, `fleet-root`, `fleet-child` (+ `data-session-id`),
 * `fleet-child-transcript`.
 */
export function FleetTree({ roots, workspaceId }: { roots: FleetNode[]; workspaceId: string }) {
  if (fleetMemberCount(roots) === 0) return null;
  return (
    <div data-testid="fleet-tree" className="flex flex-col gap-2">
      {roots
        .filter((root) => root.children.length > 0)
        .map((root) => (
          <FleetRoot key={root.entry.session.session_id} root={root} workspaceId={workspaceId} />
        ))}
    </div>
  );
}

function FleetRoot({ root, workspaceId }: { root: FleetNode; workspaceId: string }) {
  const [open, setOpen] = useState(false);
  const count = root.children.length;
  return (
    <div data-testid="fleet-root" className="flex flex-col">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-fit items-center gap-2 rounded-md py-1 text-sm text-muted-foreground transition-colors hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        )}
      >
        <Users aria-hidden className="size-4 shrink-0" />
        <span>
          {count} sub-agent{count > 1 ? "s" : ""}
        </span>
        <ChevronDownIcon
          aria-hidden
          className={cn("size-4 shrink-0 transition-transform", !open && "-rotate-90")}
        />
      </button>
      {open && (
        <ul className="flex flex-col gap-1.5 ps-6 pt-1">
          {root.children.map((child) => (
            <FleetChild
              key={child.entry.session.session_id}
              node={child}
              workspaceId={workspaceId}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function FleetChild({ node, workspaceId }: { node: FleetNode; workspaceId: string }) {
  const [open, setOpen] = useState(false);
  const { entry } = node;
  const live = AgentLiveStatus.of(entry.activity);
  const label = entry.activity.title || "Sub-agent";
  const description = entry.activity.current_task;

  return (
    <li
      data-testid="fleet-child"
      data-session-id={entry.session.session_id}
      className="rounded-md bg-muted/30 p-2"
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "flex w-full items-start gap-1.5 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        )}
      >
        <AgentStateMark state={live.state} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-xs font-medium">{label}</span>
            {live.model && (
              <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
                {live.model}
              </Badge>
            )}
          </div>
          {description && (
            <p className="mt-0.5 truncate text-[11px] text-muted-foreground">{description}</p>
          )}
          <div className="mt-1 flex items-center gap-3">
            <Stat icon={MessagesSquare} value={live.turns} label="turns" />
            <Stat icon={Wrench} value={live.toolCalls} label="tool calls" />
          </div>
        </div>
        <ChevronDownIcon
          aria-hidden
          className={cn(
            "mt-0.5 size-3.5 shrink-0 text-muted-foreground transition-transform",
            !open && "-rotate-90",
          )}
        />
      </button>
      {open && (
        <div className="mt-2 border-t border-border/60 pt-2">
          <FleetChildTranscript workspaceId={workspaceId} sessionId={entry.session.session_id} />
        </div>
      )}
    </li>
  );
}

/**
 * A sub-agent's own conversation, fetched on expand only — the SAME
 * fetch-on-demand `/turns` hook the primary transcript uses
 * (`useSessionTurns`, `SessionDetailView`/`SessionTurnView`/`DigestEntryView`
 * unchanged), so a sub-agent's history renders through no new wire. Read-only
 * by construction (a finished sidechain has nothing left to steer): mapped
 * through the SAME pure `chatItemsFromTurns` projection the live chat panel
 * uses, then drawn with the transcript's own leaf components — never a
 * bespoke render.
 */
function FleetChildTranscript({
  workspaceId,
  sessionId,
}: {
  workspaceId: string;
  sessionId: string;
}) {
  const { data, isLoading, isError } = useSessionTurns(workspaceId, sessionId);

  if (isLoading) {
    return (
      <div data-testid="fleet-child-transcript" className="flex flex-col gap-1.5">
        <Skeleton className="h-3 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <p data-testid="fleet-child-transcript" className="text-xs text-muted-foreground">
        No transcript recorded for this sub-agent yet.
      </p>
    );
  }
  const items = chatItemsFromTurns(data.turns);
  if (items.length === 0) {
    return (
      <p data-testid="fleet-child-transcript" className="text-xs text-muted-foreground">
        No messages recorded.
      </p>
    );
  }
  return (
    <div data-testid="fleet-child-transcript" className="flex flex-col gap-2">
      {items.map((item, i) => (
        <FleetTranscriptItem key={i} item={item} />
      ))}
    </div>
  );
}

function FleetTranscriptItem({ item }: { item: ChatItem }) {
  switch (item.kind) {
    case "message":
      return (
        <div className="flex flex-col gap-0.5">
          <RoleLabel role={item.role === "user" ? "you" : "agent"} />
          <div className="text-xs leading-relaxed text-foreground">
            <Response>{item.text}</Response>
          </div>
        </div>
      );
    case "tools":
      return <ToolGroup calls={item.calls} />;
    case "note":
      return <p className="text-center text-xs text-muted-foreground">{item.text}</p>;
    case "notification":
      return (
        <p className="text-xs text-muted-foreground">
          {item.summary}
          {item.detail && <span className="block text-foreground/80">{item.detail}</span>}
        </p>
      );
    case "question":
      return <QuestionCard question={item.question} />;
    case "file-edit":
      return (
        <FileEditCard
          path={item.path}
          displayPath={item.displayPath}
          oldText={item.oldText}
          newText={item.newText}
        />
      );
    case "continuation":
      return <p className="text-center text-xs text-muted-foreground">continued session</p>;
    default:
      return null;
  }
}
