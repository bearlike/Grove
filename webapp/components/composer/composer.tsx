"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { ArrowUp, LoaderCircle, Maximize2 } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useActivityStream,
  useAgents,
  useBranches,
  useCreateWorkspace,
} from "@/lib/grove/hooks";
import { useUiStore } from "@/lib/grove/ui-store";
import { GroveProtocolError } from "@/lib/grove/client";
import type { CreateWorkspaceRequest } from "@/lib/grove/types";
import { cn } from "@/lib/utils";
import { AgentPicker } from "./agent-picker";
import { ModelPicker } from "./model-picker";
import { branchPlanReady, buildBranchPlan, ContextChips, type RepoOption } from "./context-chips";
import { kindSupportsModel } from "./models";

// The markdown preview rides streamdown — the chat panel's whole bundle cost.
// Lazy-load it so the home surface pays NOTHING until the fullscreen preview is
// actually opened (the same discipline ai-elements/response keeps for the chat).
const Response = dynamic(
  () => import("@/components/ai-elements/response").then((m) => m.Response),
  {
    ssr: false,
    loading: () => <p className="text-sm text-muted-foreground">Loading preview…</p>,
  },
);

/** Title = the first non-empty line of the prompt, trimmed. The full prompt is
 *  the initial task (a single-line prompt is acceptably both). */
function firstLine(prompt: string): string {
  for (const line of prompt.split("\n")) {
    const t = line.trim();
    if (t.length > 0) return t;
  }
  return "";
}

/**
 * The composer — the always-present hero create surface at the top of the main
 * column (issue #96 deliverable A; expanded #98). The prompt textarea IS the
 * create surface: type a task, hit Enter, a workspace is created and you're
 * routed to it. The title is auto-derived from the first prompt line; the full
 * prompt boots the agent as its first task.
 *
 * #98 additions (all on the store-backed draft so inline + fullscreen share one
 * source of truth):
 *  - Expand-on-focus: the inline textarea grows + the card lifts while focused
 *    or non-empty, relaxing back when blurred AND empty (a calm 200ms tween).
 *  - Fullscreen mode: a `Maximize` toggle opens a near-viewport Dialog with a
 *    Write|Preview split — Preview renders the prompt as Markdown via the lazy
 *    `Response` (streamdown). In fullscreen, ⌘/Ctrl+Enter submits, Enter is a
 *    newline (so long Markdown is comfortable); inline keeps Enter=submit.
 *  - Repo-follows-scope: picking a repo in the left rail (`scopeRepo`) retargets
 *    the create, so "what you're looking at" and "what you'll create in" agree.
 *
 * Takes NO props: it reads/writes the `composer` store slice and pulls its own
 * data hooks. The pickers + chips below are presentational leaves it wires up;
 * the engine validates the request, so this stays thin and surfaces the typed
 * `GroveProtocolError` refusal inline rather than re-checking client-side.
 */
export function Composer() {
  const router = useRouter();
  const composer = useUiStore((s) => s.composer);
  const patch = useUiStore((s) => s.patchComposer);
  const resetAfterCreate = useUiStore((s) => s.resetComposerAfterCreate);
  const scopeRepo = useUiStore((s) => s.scopeRepo);

  // Repos from the authoritative project list (the `/activity` snapshot), NOT the
  // workspace list — an empty / config-declared project (#95) has zero workspaces
  // but is still a valid create target, and each group carries name + root.
  const { snapshot } = useActivityStream();
  const repos: RepoOption[] = useMemo(
    () => (snapshot?.projects ?? []).map((p) => ({ root: p.repo_root, name: p.repo_name })),
    [snapshot],
  );

  const agentsQuery = useAgents(composer.repoRoot);
  const agents = useMemo(() => agentsQuery.data ?? [], [agentsQuery.data]);
  // Only fetch the branch scope the active mode actually needs.
  const local = useBranches(composer.repoRoot, "local", composer.branchMode !== "track_remote");
  const remote = useBranches(composer.repoRoot, "remote", composer.branchMode === "track_remote");

  // Default the repo to the first known one once the list resolves (snapshot
  // order = the engine's sorted roots), guarded so a manual pick stays put.
  useEffect(() => {
    if (composer.repoRoot === null && repos.length > 0) patch({ repoRoot: repos[0].root });
  }, [repos, composer.repoRoot, patch]);

  // Repo-follows-scope: when the left rail narrows to a specific repo, retarget
  // the create to it. Guarded on `scopeRepo` only — a manual repo-chip pick made
  // afterward stays put (this effect won't re-run until the scope changes again).
  useEffect(() => {
    if (scopeRepo) patch({ repoRoot: scopeRepo });
  }, [scopeRepo, patch]);

  // Default the agent to the first configured one when the list resolves / the
  // repo changes (the prior pick may not exist in the new repo's cascade).
  useEffect(() => {
    if (agents.length === 0) return;
    if (!agents.some((a) => a.name === composer.agentName)) patch({ agentName: agents[0].name });
  }, [agents, composer.agentName, patch]);

  const selectedAgent = agents.find((a) => a.name === composer.agentName) ?? null;
  const showModel = kindSupportsModel(selectedAgent?.kind);

  const create = useCreateWorkspace();

  const [focused, setFocused] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  // Expanded once the field is engaged: focused OR holding a draft. The tween is
  // the "slightly animate into an expanded state" the brief asked for.
  const expanded = focused || composer.prompt.trim().length > 0;

  const title = firstLine(composer.prompt);
  const canSubmit =
    title.length > 0 &&
    Boolean(composer.repoRoot) &&
    Boolean(composer.agentName) &&
    branchPlanReady(composer);

  function submit() {
    if (!canSubmit || !composer.repoRoot || !composer.agentName || create.isPending) return;
    const req: CreateWorkspaceRequest = {
      agent_name: composer.agentName,
      title,
      // The full prompt is the agent's first task — title is just its first line.
      initial_prompt: composer.prompt.trim() || null,
      // null model (default) is omitted by JSON; an explicit/custom id rides through.
      model: showModel ? composer.model : null,
      branch_plan: buildBranchPlan(composer),
      skip_init: composer.skipInit,
      repo_root: composer.repoRoot,
    };
    create.mutate(req, {
      onSuccess: (ws) => {
        setFullscreen(false);
        resetAfterCreate();
        router.push(`/w/${ws.id}`);
      },
    });
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter submits, Shift+Enter newlines — the inline composer contract.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  function onFullscreenKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // In fullscreen, Enter is a newline (long Markdown); ⌘/Ctrl+Enter submits.
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  const error = create.error;
  const errorText =
    error instanceof GroveProtocolError
      ? error.message
      : error
        ? "Could not reach the daemon."
        : null;

  return (
    <section aria-label="Create a workspace" className="mx-auto w-full max-w-3xl">
      <Card
        className={cn(
          "rounded-xl bg-card p-3 transition-[box-shadow,border-color,transform] duration-200",
          expanded ? "border-border shadow-md" : "border-border/70 shadow-sm",
        )}
      >
        <textarea
          data-testid="composer-prompt"
          aria-label="Describe a task to start a new workspace"
          placeholder="Describe a task to start a new workspace…"
          value={composer.prompt}
          onChange={(e) => patch({ prompt: e.target.value })}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          className={cn(
            "w-full resize-none bg-transparent px-1 pt-1 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground focus-visible:outline-none",
            "max-h-72 transition-[min-height] duration-200 ease-out",
            expanded ? "min-h-28" : "min-h-12",
          )}
        />

        <div className="mt-2 flex items-center gap-2">
          {/* Quiet selector pills — everything outline/ghost; never --primary. */}
          <AgentPicker
            value={composer.agentName}
            options={agents}
            onChange={(name) => patch({ agentName: name })}
          />
          {showModel && selectedAgent ? (
            <ModelPicker
              value={composer.model}
              kind={selectedAgent.kind}
              onChange={(model) => patch({ model })}
            />
          ) : null}

          <div className="ml-auto flex items-center gap-1">
            {/* Quiet fullscreen affordance — write long Markdown with a preview. */}
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              data-testid="composer-fullscreen"
              aria-label="Open fullscreen editor"
              title="Fullscreen editor"
              onClick={() => setFullscreen(true)}
            >
              <Maximize2 />
            </Button>

            {/* The single high-contrast affordance in the view — filled terracotta. */}
            <button
              type="button"
              data-testid="composer-send"
              aria-label="Create workspace"
              disabled={!canSubmit || create.isPending}
              onClick={submit}
              className={cn(
                "inline-flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm",
                "transition-colors duration-200 hover:bg-primary/90 active:bg-primary/95",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                "disabled:pointer-events-none disabled:opacity-50",
              )}
            >
              {create.isPending ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
              ) : (
                <ArrowUp className="size-4" aria-hidden />
              )}
            </button>
          </div>
        </div>
      </Card>

      <ContextChips
        draft={composer}
        patch={patch}
        repos={repos}
        localBranches={local.data ?? []}
        remoteBranches={remote.data ?? []}
      />

      {errorText ? (
        <p
          role="alert"
          data-testid="composer-error"
          className="mt-2 rounded-md border border-[var(--status-error)]/40 bg-[var(--status-error)]/10 p-2 text-sm text-foreground"
        >
          {errorText}
        </p>
      ) : null}

      {/* ── Fullscreen editor: roomy Markdown writing + live preview ────────── */}
      <Dialog open={fullscreen} onOpenChange={setFullscreen}>
        <DialogContent className="flex h-[88vh] max-h-[88vh] w-[calc(100%-2rem)] max-w-4xl flex-col gap-0 overflow-hidden p-0">
          <DialogHeader className="border-b border-border px-4 py-3 pr-12">
            <DialogTitle className="text-sm font-semibold">Compose task</DialogTitle>
            <DialogDescription className="text-xs">
              Markdown supported · {title ? `title: “${title}”` : "the first line becomes the title"} ·
              ⌘/Ctrl+Enter to create
            </DialogDescription>
          </DialogHeader>

          <Tabs defaultValue="write" className="flex min-h-0 flex-1 flex-col px-4 pt-3">
            <TabsList className="h-8 self-start p-0.5">
              <TabsTrigger value="write" className="h-7 text-xs">
                Write
              </TabsTrigger>
              <TabsTrigger value="preview" data-testid="composer-preview-tab" className="h-7 text-xs">
                Preview
              </TabsTrigger>
            </TabsList>

            <TabsContent value="write" className="mt-3 min-h-0 flex-1">
              <textarea
                data-testid="composer-prompt-fullscreen"
                aria-label="Describe a task (fullscreen)"
                placeholder="Describe the task… Markdown supported."
                value={composer.prompt}
                onChange={(e) => patch({ prompt: e.target.value })}
                onKeyDown={onFullscreenKeyDown}
                autoFocus
                className="h-full w-full resize-none rounded-lg border border-border bg-background p-4 font-mono text-[13px] leading-relaxed text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              />
            </TabsContent>

            <TabsContent
              value="preview"
              className="mt-3 min-h-0 flex-1 overflow-auto rounded-lg border border-border bg-background p-4"
            >
              {composer.prompt.trim() ? (
                <Response>{composer.prompt}</Response>
              ) : (
                <p className="text-sm italic text-muted-foreground">Nothing to preview yet.</p>
              )}
            </TabsContent>
          </Tabs>

          <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-3">
            <span className="truncate text-xs text-muted-foreground">
              {selectedAgent ? `Agent: ${selectedAgent.name}` : "No agent selected"}
            </span>
            <Button
              type="button"
              size="sm"
              data-testid="composer-send-fullscreen"
              disabled={!canSubmit || create.isPending}
              onClick={submit}
            >
              {create.isPending ? (
                <LoaderCircle className="size-4 animate-spin" aria-hidden />
              ) : (
                <ArrowUp className="size-4" aria-hidden />
              )}
              Create workspace
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
