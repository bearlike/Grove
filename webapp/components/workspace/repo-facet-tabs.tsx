"use client";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { WorkspaceCard } from "./card";
import { WorkspaceCardModel } from "@/lib/grove/workspace-card";
import { RepoFacet } from "@/lib/grove/repo-facet";
import type { WorkspaceStateView, WorkspacePeekView } from "@/lib/grove/types";

export function RepoFacetTabs({
  workspaces,
  peeks,
}: {
  workspaces: WorkspaceStateView[];
  peeks: Map<string, WorkspacePeekView | undefined>;
}) {
  const facets = RepoFacet.groupByRepo(workspaces);
  const all = workspaces;

  return (
    <Tabs defaultValue="__all__" className="w-full">
      <TabsList className="w-full justify-start overflow-x-auto">
        <TabsTrigger value="__all__" className="gap-2">
          All
          <Badge variant="secondary" className="rounded-full">{all.length}</Badge>
        </TabsTrigger>
        {facets.map((f) => (
          <TabsTrigger key={f.repoRoot} value={f.repoRoot} title={f.repoRoot} className="gap-2">
            {f.repoName}
            <Badge variant="secondary" className="rounded-full">{f.total}</Badge>
          </TabsTrigger>
        ))}
      </TabsList>
      <TabsContent value="__all__">
        <Grid views={all} peeks={peeks} />
      </TabsContent>
      {facets.map((f) => (
        <TabsContent key={f.repoRoot} value={f.repoRoot}>
          <Grid views={f.workspaces as WorkspaceStateView[]} peeks={peeks} />
        </TabsContent>
      ))}
    </Tabs>
  );
}

function Grid({
  views,
  peeks,
}: {
  views: WorkspaceStateView[];
  peeks: Map<string, WorkspacePeekView | undefined>;
}) {
  if (views.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
        No workspaces here yet.
      </div>
    );
  }
  // `auto-rows-fr` makes every row the height of its tallest card so a
  // long branch name in one card lifts every neighbor to the same height.
  return (
    <div className="grid auto-rows-fr grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {views.map((v) => (
        <WorkspaceCard
          key={v.id}
          model={WorkspaceCardModel.fromState(v)}
          peek={peeks.get(v.id)}
        />
      ))}
    </div>
  );
}
