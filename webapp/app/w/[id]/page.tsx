"use client";

import { use } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Header } from "@/components/layout/header";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { StatusBadge } from "@/components/workspace/status-badge";
import { PlacementBadge } from "@/components/workspace/placement-badge";
import { StatTrio } from "@/components/workspace/stat-trio";
import { CommitList } from "@/components/workspace/commit-list";
import { PeekSnapshot } from "@/components/workspace/peek-snapshot";
import { SessionsPanel } from "@/components/workspace/sessions-panel";
import { RelativeTime } from "@/components/shared/relative-time";
import {
  useWorkspaceCommits,
  useWorkspacePeek,
  useWorkspaceSessions,
} from "@/lib/grove/hooks";

const PANEL_TITLE_CLS =
  "text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground";

export default function DetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data, isLoading, isError, error, dataUpdatedAt } = useWorkspacePeek(id);
  const { data: commits, isLoading: commitsLoading } = useWorkspaceCommits(id);
  const { data: sessions, isLoading: sessionsLoading } = useWorkspaceSessions(id);

  // Layout intent: the agent terminal is the page's primary artifact, so on
  // lg+ the page is a viewport-fill flex column and the side-by-side row
  // (summary + agent) flexes into the remaining height. PeekSnapshot fills
  // its parent (h-full min-h-[28rem]) and the column split is 4/8 so the
  // terminal gets ~67% of width — typical 80–100 col tmux output fits without
  // wrapping. On mobile the grid stacks naturally and each panel uses its
  // own min-h floor; no viewport-fill needed.
  return (
    <>
      <Header />
      <main className="mx-auto flex w-full max-w-screen-xl flex-col gap-4 p-4 pb-[env(safe-area-inset-bottom)] lg:min-h-[calc(100dvh-5.25rem)]">
        <Button asChild variant="ghost" size="sm" className="self-start">
          <Link href="/" aria-label="Back to all workspaces">
            <ArrowLeft />
            <span>All workspaces</span>
          </Link>
        </Button>

        {isLoading && <DetailSkeleton />}
        {isError && (
          <div
            role="alert"
            className="rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 p-4 text-sm text-foreground"
          >
            Could not load workspace: {String((error as Error).message)}
          </div>
        )}

        {data && (
          <>
            <Card data-testid="identity-panel">
              <CardHeader className="flex flex-row items-start justify-between gap-4">
                <div className="min-w-0 space-y-1.5">
                  <CardTitle className="truncate text-xl font-semibold tracking-tight">
                    {data.state.title}
                  </CardTitle>
                  <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-sm text-muted-foreground">
                    <Badge variant="outline" className="font-mono text-[var(--ref-branch)] border-[var(--ref-branch)]/40">
                      {data.state.branch}
                    </Badge>
                    <span className="text-muted-foreground/70">from</span>
                    <Badge variant="outline" className="font-mono">{data.state.base_branch}</Badge>
                    <span aria-hidden className="text-muted-foreground/40">·</span>
                    <span className="text-muted-foreground/70">agent</span>
                    <Badge variant="outline" className="font-mono text-[var(--ref-info)] border-[var(--ref-info)]/40">
                      {data.state.agent_name}
                    </Badge>
                  </p>
                  {data.state.description && (
                    <p className="pt-1 text-sm leading-relaxed text-foreground/90">
                      {data.state.description}
                    </p>
                  )}
                  {data.state.init_duration_ms != null && (
                    <p className="font-mono text-xs text-muted-foreground">
                      init {(data.state.init_duration_ms / 1000).toFixed(1)}s
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  <PlacementBadge placement={data.state.placement} />
                  <StatusBadge status={data.state.status} />
                </div>
              </CardHeader>
            </Card>

            <div className="grid grid-cols-1 gap-4 lg:auto-rows-fr lg:grid-cols-12 lg:flex-1">
              <Card className="flex flex-col lg:col-span-4" data-testid="summary-panel">
                <CardHeader>
                  <CardTitle className={PANEL_TITLE_CLS}>Summary</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-1 flex-col gap-4">
                  <StatTrio
                    ahead={data.base_ahead}
                    behind={data.base_behind}
                    dirty={data.dirty_files}
                  />
                  <p className="text-xs text-muted-foreground">
                    {data.diff_added > 0 || data.diff_removed > 0 ? (
                      <>
                        <span className="font-medium text-[var(--ref-add)]">
                          +{data.diff_added}
                        </span>
                        {" / "}
                        <span className="font-medium text-[var(--ref-remove)]">
                          −{data.diff_removed}
                        </span>
                        {" lines changed"}
                      </>
                    ) : (
                      "No uncommitted changes."
                    )}
                  </p>
                  <Separator />
                  <div className="flex min-h-0 flex-1 flex-col gap-2">
                    <CardTitle className={PANEL_TITLE_CLS}>Commits since fork</CardTitle>
                    <CommitList commits={commits} isLoading={commitsLoading} />
                  </div>
                </CardContent>
              </Card>

              <Card className="flex flex-col lg:col-span-8" data-testid="agent-panel">
                <CardHeader className="flex flex-row items-center justify-between gap-2">
                  <CardTitle className={PANEL_TITLE_CLS}>Agent</CardTitle>
                  <span className="text-[11px] text-muted-foreground">
                    Updated <RelativeTime iso={data.snapshot_taken_at} />
                  </span>
                </CardHeader>
                <CardContent className="flex min-h-0 flex-1 flex-col">
                  <PeekSnapshot
                    snapshot={data.agent_snapshot}
                    takenAt={data.snapshot_taken_at}
                  />
                </CardContent>
              </Card>
            </div>

            <Card data-testid="sessions-card">
              <CardHeader>
                <CardTitle className={PANEL_TITLE_CLS}>Sessions</CardTitle>
              </CardHeader>
              <CardContent>
                <SessionsPanel
                  workspaceId={id}
                  sessions={sessions}
                  isLoading={sessionsLoading}
                />
              </CardContent>
            </Card>
          </>
        )}

        {data && (
          <p
            className="flex items-center justify-center gap-2 text-[11px] text-muted-foreground"
            aria-live="polite"
          >
            <span
              aria-hidden
              className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--status-active)]"
            />
            Polling every 2 s · last refresh {new Date(dataUpdatedAt).toLocaleTimeString()}
          </p>
        )}
      </main>
    </>
  );
}

function DetailSkeleton() {
  return (
    <>
      <Skeleton className="h-32 w-full" />
      <div className="grid grid-cols-1 gap-4 lg:auto-rows-fr lg:grid-cols-12 lg:flex-1">
        <Skeleton className="h-64 lg:col-span-4 lg:h-auto" />
        <Skeleton className="h-64 lg:col-span-8 lg:h-auto" />
      </div>
    </>
  );
}
