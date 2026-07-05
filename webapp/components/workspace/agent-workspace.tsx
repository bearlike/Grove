"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { Columns2, Square } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { TerminalPane } from "@/components/terminal/terminal-pane";
import { cn } from "@/lib/utils";

// Streamdown is a ~460 kB async chunk and the only consumer is the chat panel —
// keep it behind `next/dynamic` here (the component that renders it) so the
// dynamic boundary travels with the leaf, not the page.
const ChatPanel = dynamic(
  () => import("@/components/chat/chat-panel").then((m) => m.ChatPanel),
  { ssr: false, loading: () => <Skeleton className="h-full min-h-[20rem] w-full" /> },
);

type AgentTab = "transcript" | "terminal";
type View = "tabs" | "split";

/** SSR-safe `min-width` match; jsdom's matchMedia stub reports false → tabs. */
function useMinWidth(px: number): boolean {
  const [match, setMatch] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia(`(min-width:${px}px)`);
    const sync = () => setMatch(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, [px]);
  return match;
}

/**
 * The dominant surface of the detail page: the agent transcript and the live
 * terminal. Transcript is the default (the revamp's headline — the conversation
 * sits first); the terminal is the second tab, carrying its own live-capture
 * badge. On lg+ a split view shows both panes side by side behind a draggable
 * `Resizable` handle (the canonical primitive, never a hand-rolled resizer),
 * with the ratio persisted via `autoSaveId`. Below lg the split is unreachable
 * — a horizontal split on a phone is wrong — so the surface stays tabs-only and
 * the toggle is hidden.
 *
 * `defaultTab` is derived by the page (Transcript when sessions exist, else
 * Terminal, `null` while loading); a user's explicit tab/view choice always
 * wins after. Seams: `agent-panel`, `agent-tabs`, `tab-transcript`,
 * `tab-terminal`, `view-tabs`, `view-split`, `subagent-badge`.
 */
export function AgentWorkspace({
  workspaceId,
  snapshot,
  takenAt,
  tmuxSession,
  defaultTab,
  subagents,
}: {
  workspaceId: string;
  snapshot: string | null;
  takenAt: string | null;
  tmuxSession: string;
  defaultTab: AgentTab | null;
  subagents: number;
}) {
  const isLg = useMinWidth(1024);
  const [chosenTab, setChosenTab] = useState<AgentTab | null>(null);
  const [view, setView] = useState<View>("tabs");
  const tab = chosenTab ?? defaultTab;
  const showSplit = isLg && view === "split";

  const transcript = <ChatPanel workspaceId={workspaceId} />;
  const terminal = <TerminalPane snapshot={snapshot} takenAt={takenAt} target={tmuxSession} />;

  const rightCluster = (
    <div className="flex shrink-0 items-center gap-2">
      {subagents > 0 && (
        <Badge variant="secondary" data-testid="subagent-badge">
          {subagents} background agent{subagents === 1 ? "" : "s"}
        </Badge>
      )}
      {isLg && (
        <div className="flex items-center gap-0.5 rounded-md border border-border bg-muted/40 p-0.5">
          <ViewToggle
            testid="view-tabs"
            label="Single pane"
            active={view === "tabs"}
            onClick={() => setView("tabs")}
          >
            <Square className="size-4" />
          </ViewToggle>
          <ViewToggle
            testid="view-split"
            label="Split view"
            active={view === "split"}
            onClick={() => setView("split")}
          >
            <Columns2 className="size-4" />
          </ViewToggle>
        </div>
      )}
    </div>
  );

  return (
    <div
      data-testid="agent-panel"
      className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 p-4"
    >
      {showSplit ? (
        <>
          <div className="flex items-center justify-end gap-2">{rightCluster}</div>
          <ResizablePanelGroup
            direction="horizontal"
            autoSaveId="grove-detail-split"
            className="min-h-0 flex-1"
          >
            <ResizablePanel defaultSize={55} minSize={30} className="flex min-h-0 min-w-0 flex-col pr-1.5">
              {transcript}
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={45} minSize={25} className="flex min-h-0 min-w-0 flex-col pl-1.5">
              {terminal}
            </ResizablePanel>
          </ResizablePanelGroup>
        </>
      ) : (
        <Tabs
          value={tab ?? ""}
          onValueChange={(v) => setChosenTab(v as AgentTab)}
          className="flex min-h-0 min-w-0 flex-1 flex-col gap-3"
          data-testid="agent-tabs"
        >
          <div className="flex items-center justify-between gap-2">
            <TabsList>
              <TabsTrigger value="transcript" data-testid="tab-transcript">
                Transcript
              </TabsTrigger>
              <TabsTrigger value="terminal" data-testid="tab-terminal" className="gap-1.5">
                Terminal
                <span
                  aria-hidden
                  className="size-1.5 rounded-full bg-[var(--status-active)] motion-safe:animate-pulse"
                />
              </TabsTrigger>
            </TabsList>
            {rightCluster}
          </div>
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {tab === null && <Skeleton className="h-full min-h-[20rem] w-full" />}
            <TabsContent
              value="transcript"
              className="mt-0 flex min-h-0 min-w-0 flex-1 flex-col"
            >
              {transcript}
            </TabsContent>
            <TabsContent
              value="terminal"
              className="mt-0 flex min-h-0 min-w-0 flex-1 flex-col"
            >
              {terminal}
            </TabsContent>
          </div>
        </Tabs>
      )}
    </div>
  );
}

function ViewToggle({
  active,
  onClick,
  label,
  testid,
  children,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  testid: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      data-testid={testid}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex size-7 items-center justify-center rounded transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        active ? "bg-card text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
