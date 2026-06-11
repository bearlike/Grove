"use client";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { WorkspaceCard } from "./card";
import { WorkspaceCardModel } from "@/lib/grove/workspace-card";
import { RepoFacet } from "@/lib/grove/repo-facet";
import type { WorkspaceActivityView, WorkspaceStateView } from "@/lib/grove/types";

export function RepoFacetTabs({ activities }: { activities: WorkspaceActivityView[] }) {
  const facets = RepoFacet.groupActivityByRepo(activities);
  // RepoFacet groups/sorts on the embedded state; re-pair each member with its
  // full activity view by id so the card gets git stats. A miss can't happen by
  // construction, but degrades to a stats-less card rather than throwing.
  const byId = new Map(activities.map((a) => [a.state.id, a]));
  const toModels = (views: ReadonlyArray<WorkspaceStateView>): WorkspaceCardModel[] =>
    views.map((v) => {
      const a = byId.get(v.id);
      return a ? WorkspaceCardModel.fromActivity(a) : WorkspaceCardModel.fromState(v);
    });
  const all = activities.map((a) => WorkspaceCardModel.fromActivity(a));

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
        <Grid models={all} />
      </TabsContent>
      {facets.map((f) => (
        <TabsContent key={f.repoRoot} value={f.repoRoot}>
          <Grid models={toModels(f.workspaces)} />
        </TabsContent>
      ))}
    </Tabs>
  );
}

function Grid({ models }: { models: WorkspaceCardModel[] }) {
  if (models.length === 0) {
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
      {models.map((m) => (
        <WorkspaceCard key={m.state.id} model={m} />
      ))}
    </div>
  );
}
