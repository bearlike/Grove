"use client";

import { useEffect, useId, useMemo, useState } from "react";

import { ErrorState } from "@/components/elements/error-state";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ModelSelector } from "@/components/assistant-ui/model-selector";
import { agentDescription } from "@/components/grove/launch/controls/agent-pill";
import { modelOptions } from "@/components/grove/model-option";
import type { CreateWorkspaceRequest } from "@/lib/grove/api";
import { useAgents, useBranches, useModels } from "@/lib/grove/hooks";
import { AUTO_BRANCH, NEW_BRANCH, ROOT_BRANCH, branchOptionValue, toBranchPlan } from "./branch-plan";
import { useCreateWorkspace } from "./use-fleet";
import type { ProjectGroup, Runtime } from "./types";

/**
 * Sentinel for "don't send this field". Radix `Select` cannot hold an empty
 * string, and the tri-state fields (runtime, brief) genuinely have three
 * meanings — on, off, and *let the project's config cascade answer*. Sending
 * `null` is what preserves the third one.
 */
const INHERIT = "inherit";

/**
 * "Agent default" is a VALUE — precisely what an omitted `model` produces —
 * so it is a row of the selector rather than a placeholder on the trigger.
 * Declared once because both the rendered list and the label-resolution list
 * must name it identically; two spellings would show one thing and select
 * another.
 */
const AGENT_DEFAULT_ROW = { id: INHERIT, name: "Agent default" } as const;

const TICKET_PROVIDERS = ["gitea", "github", "linear"] as const;
type TicketProvider = (typeof TICKET_PROVIDERS)[number];

/**
 * A `Select`'s explanation for why it is offering nothing.
 *
 * Deliberately NOT a `SelectItem`: a note must not be selectable, and even a
 * disabled item costs a keyboard user a stop on a string they cannot act on.
 */
function PickerNote({ children }: { children: React.ReactNode }): React.ReactNode {
  return (
    <p role="status" className="px-2 py-1.5 text-xs text-muted-foreground">
      {children}
    </p>
  );
}

/** A labelled control. Six fields with identical anatomy earn exactly one wrapper. */
function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}): React.ReactNode {
  // `aria-labelledby` rather than `htmlFor`: a field can hold two controls (the
  // branch picker plus its name box), and there is no single input to point at.
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5" role="group" aria-labelledby={id}>
      <Label id={id}>{label}</Label>
      {children}
      {hint ? <span className="text-xs">{hint}</span> : null}
    </div>
  );
}

/**
 * The one place a workspace is born.
 *
 * Every field except the title and the agent is optional and falls through to
 * the config cascade, which is the difference between a form that asks nine
 * questions and one that asks two — and the cascade already holds better
 * answers than a dashboard default would.
 */
export function CreateWorkspaceDialog({
  open,
  onOpenChange,
  projects,
  repoRoot,
  onRepoRootChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projects: readonly ProjectGroup[];
  repoRoot: string;
  onRepoRootChange: (repoRoot: string) => void;
}): React.ReactNode {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>New workspace</DialogTitle>
          <DialogDescription>
            A git worktree, a branch and an agent, isolated from everything else you are running.
          </DialogDescription>
        </DialogHeader>
        {/* The form is a CHILD so that a closed dialog costs nothing. Radix
            renders no `DialogContent` while closed, so the queries below only
            run once someone opens it — as one component this fetched the repo's
            agents and both branch lists on EVERY route load, three requests for
            a form nobody had asked for, and re-rendered with the fleet's
            activity tick for the whole life of the page. */}
        <CreateWorkspaceForm
          projects={projects}
          repoRoot={repoRoot}
          onRepoRootChange={onRepoRootChange}
          onCreated={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

function CreateWorkspaceForm({
  projects,
  repoRoot,
  onRepoRootChange,
  onCreated,
}: {
  projects: readonly ProjectGroup[];
  repoRoot: string;
  onRepoRootChange: (repoRoot: string) => void;
  onCreated: () => void;
}): React.ReactNode {
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [agentName, setAgentName] = useState("");
  const [model, setModel] = useState(INHERIT);
  const [runtime, setRuntime] = useState<Runtime | typeof INHERIT>(INHERIT);
  const [brief, setBrief] = useState(INHERIT);
  const [sessionMode, setSessionMode] = useState(INHERIT);
  const [branchChoice, setBranchChoice] = useState(AUTO_BRANCH);
  const [branchName, setBranchName] = useState("");
  const [ticketProvider, setTicketProvider] = useState<TicketProvider>("gitea");
  const [ticketId, setTicketId] = useState("");

  // `|| null` is load-bearing, not tidiness: these hooks treat `null` as "no
  // repo chosen yet" where this form uses `""`, so passing the bare string
  // would fetch `/agents?repo=` before the user has picked anything.
  const agents = useAgents(repoRoot || null);
  const models = useModels(repoRoot || null, agentName || null);
  const localBranches = useBranches(repoRoot || null, "local");
  const remoteBranches = useBranches(repoRoot || null, "remote");
  const create = useCreateWorkspace();

  const agent = useMemo(
    () => agents.data?.find((candidate) => candidate.name === agentName),
    [agents.data, agentName],
  );

  const { namespace: modelNamespace, options: modelRows } = useMemo(
    () => modelOptions(models.data ?? []),
    [models.data],
  );
  // The sentinel is a real selectable value, so it belongs in the list the
  // selector resolves labels from — see the comment at `ModelSelector.Root`.
  const selectableRows = useMemo(
    () => [AGENT_DEFAULT_ROW, ...modelRows],
    [modelRows],
  );

  // The agent list is per-repo, so the chosen agent can stop existing when the
  // project changes; fall back to the first one the new repo offers.
  useEffect(() => {
    const available = agents.data;
    if (!available || available.length === 0) return;
    if (available.some((candidate) => candidate.name === agentName)) return;
    setAgentName(available[0]!.name);
    setModel(INHERIT);
    setSessionMode(INHERIT);
  }, [agents.data, agentName]);
  // The mode's default belongs to the ENTRY, so a choice made against one
  // agent is not an answer about the next (the landing page's RULE 1b).
  const hasSessionMode = agent !== undefined && (agent.kind === "claude_code" || agent.kind === "codex");

  const submit = (event: React.FormEvent): void => {
    event.preventDefault();
    const request: CreateWorkspaceRequest = {
      agent_name: agentName,
      title: title.trim(),
      repo_root: repoRoot,
      // Root placement carries skip-init, the way the TUI's create screen does:
      // an init script written for a fresh worktree can be destructive in the
      // live repo root everyone else is standing in.
      skip_init: branchChoice === ROOT_BRANCH,
      branch_plan: toBranchPlan(branchChoice, branchName),
      ...(model === INHERIT ? {} : { model }),
      ...(runtime === INHERIT ? {} : { runtime }),
      ...(brief === INHERIT ? {} : { brief: brief === "on" }),
      ...(sessionMode === INHERIT || !hasSessionMode ? {} : { native: sessionMode === "native" }),
      ...(prompt.trim() === "" ? {} : { initial_prompt: prompt.trim() }),
      ...(ticketId.trim() === ""
        ? {}
        : { ticket: { provider: ticketProvider, id: ticketId.trim(), kind: "issue" } }),
    };
    create.mutate(request, { onSuccess: onCreated });
  };

  const ready = title.trim() !== "" && agentName !== "" && repoRoot !== "";

  // Three different silences look identical inside a `Select`, and the agent one
  // is load-bearing: Create stays disabled until an agent is chosen, so a failed
  // `/agents` read used to present an empty menu beside a dead button with
  // nothing anywhere saying why. Branches are softer — Auto and New keep working
  // — but a picker that has quietly stopped listing this repo's branches is a
  // picker a reader will assume has none.
  const agentNote = agents.isPending
    ? "Loading agents…"
    : agents.isError
      ? "Could not read this project's agents."
      : (agents.data ?? []).length === 0
        ? "This project has no agent configured."
        : null;

  const branchesPending = localBranches.isPending || remoteBranches.isPending;
  const branchNote = branchesPending
    ? "Loading branches…"
    : localBranches.isError || remoteBranches.isError
      ? "Could not read this project's branches. Auto and New still work."
      : null;

  return (
    <form className="flex flex-col gap-4" onSubmit={submit} data-testid="create-workspace-form">
          <Field label="Project">
            <Select value={repoRoot} onValueChange={onRepoRootChange}>
              <SelectTrigger className="w-full" data-testid="create-project">
                <SelectValue placeholder="Pick a repo" />
              </SelectTrigger>
              <SelectContent>
                {projects.map((project) => (
                  <SelectItem key={project.repo_root} value={project.repo_root}>
                    {project.repo_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field label="Title">
            <Input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="What this workspace is for"
              data-testid="create-title"
              autoFocus
            />
          </Field>

          <Field label="Task" hint="Sent to the agent as its first message.">
            <Input
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Optional — start the agent off with something"
              data-testid="create-prompt"
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Agent">
              <Select value={agentName} onValueChange={setAgentName}>
                <SelectTrigger className="w-full" data-testid="create-agent">
                  <SelectValue placeholder={agentNote ?? "Pick an agent"} />
                </SelectTrigger>
                <SelectContent>
                  {agentNote ? (
                    <PickerNote>{agentNote}</PickerNote>
                  ) : (
                    (agents.data ?? []).map((candidate) => (
                      <SelectItem key={candidate.name} value={candidate.name}>
                        <span className="flex min-w-0 items-baseline gap-2">
                          <span className="truncate">{candidate.name}</span>
                          <span className="shrink-0 text-xs text-content-tertiary">
                            {agentDescription(candidate)}
                          </span>
                        </span>
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
              {agents.isError ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="self-start"
                  onClick={() => void agents.refetch()}
                  disabled={agents.isFetching}
                  data-testid="create-agent-retry"
                >
                  {agents.isFetching ? "Retrying…" : "Try again"}
                </Button>
              ) : null}
            </Field>

            <Field label="Model">
              {/* `models` must contain EVERY selectable row, including the
                  sentinel: the bare Trigger resolves its label by looking
                  `value` up in this list, so a catalog-only list made the
                  default render as the vendor's "Select model" placeholder —
                  a control claiming nothing was chosen while the request it
                  builds is the resolved default. The rendered rows below stay
                  composed separately so the sentinel keeps its place above the
                  namespace group. */}
              <ModelSelector.Root models={selectableRows} value={model} onValueChange={setModel}>
                {/* Trigger appends its own chevron, so it cannot compose the
                    outlined Button through `asChild`. */}
                <ModelSelector.Trigger variant="outline" className="w-full" data-testid="create-model" />
                <ModelSelector.Content searchable aria-label="Choose model">
                  <ModelSelector.Search />
                  <ModelSelector.List>
                    <ModelSelector.Empty />
                    {/* Agent default is a VALUE, not a placeholder: an omitted
                        `model` is exactly what the engine resolves, so it sits
                        above the catalog rather than inside its namespace group. */}
                    <ModelSelector.Item model={AGENT_DEFAULT_ROW} />
                    <ModelSelector.Group heading={modelNamespace || undefined}>
                      {modelRows.map((option) => (
                        // `title` keeps the full id reachable: the visible label has
                        // had the shared namespace folded out of it.
                        <ModelSelector.Item key={option.id} model={option} title={option.id} />
                      ))}
                    </ModelSelector.Group>
                  </ModelSelector.List>
                </ModelSelector.Content>
              </ModelSelector.Root>
            </Field>

            <Field label="Runtime">
              <Select
                value={runtime}
                onValueChange={(value) => setRuntime(value as Runtime | typeof INHERIT)}
              >
                <SelectTrigger className="w-full" data-testid="create-runtime">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={INHERIT}>Project default</SelectItem>
                  <SelectItem value="host">Host</SelectItem>
                  <SelectItem value="container">Container</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            <Field label="Brief">
              <Select value={brief} onValueChange={setBrief}>
                <SelectTrigger className="w-full" data-testid="create-brief">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={INHERIT}>Project default</SelectItem>
                  <SelectItem value="on">Send Grove&apos;s brief</SelectItem>
                  <SelectItem value="off">No brief</SelectItem>
                </SelectContent>
              </Select>
            </Field>

            {hasSessionMode ? (
              <Field
                label="Session mode"
                hint={`This entry's default is ${agent.native ? "a native session" : "the terminal"}.`}
              >
                <Select
                  value={sessionMode}
                  onValueChange={setSessionMode}
                >
                  <SelectTrigger className="w-full" data-testid="create-session-mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={INHERIT}>
                      Entry default ({agent.native ? "native session" : "terminal"})
                    </SelectItem>
                    <SelectItem value="native">Native session — headless, Grove-owned</SelectItem>
                    <SelectItem value="terminal">Terminal — the agent&apos;s interactive UI</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
            ) : null}
          </div>

          <Field label="Branch">
            <Select value={branchChoice} onValueChange={setBranchChoice}>
              <SelectTrigger className="w-full" data-testid="create-branch">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={AUTO_BRANCH}>Auto — name it after the title</SelectItem>
                <SelectItem value={NEW_BRANCH}>New branch…</SelectItem>
                {/*
                  Root states its cost in the option, never after it is chosen —
                  a consequence a user meets when `pause` refuses is a
                  consequence Grove hid.
                */}
                <SelectItem value={ROOT_BRANCH}>
                  Work in place — no worktree, no isolation, no pause/resume
                </SelectItem>
                {branchNote ? <PickerNote>{branchNote}</PickerNote> : null}
                {(localBranches.data ?? []).map((branch) => (
                  <SelectItem key={`local:${branch.name}`} value={branchOptionValue(branch)}>
                    {branch.name}
                  </SelectItem>
                ))}
                {(remoteBranches.data ?? []).map((branch) => (
                  <SelectItem key={`remote:${branch.name}`} value={branchOptionValue(branch)}>
                    {branch.name} (remote)
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {branchChoice === NEW_BRANCH ? (
              <Input
                value={branchName}
                onChange={(event) => setBranchName(event.target.value)}
                placeholder="branch name"
                data-testid="create-branch-name"
              />
            ) : null}
          </Field>

          <Field label="Ticket" hint="Links the workspace to an issue and publishes its progress there.">
            <div className="flex gap-2">
              <Select
                value={ticketProvider}
                onValueChange={(value) => setTicketProvider(value as TicketProvider)}
              >
                <SelectTrigger data-testid="create-ticket-provider">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TICKET_PROVIDERS.map((provider) => (
                    <SelectItem key={provider} value={provider}>
                      {provider}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                value={ticketId}
                onChange={(event) => setTicketId(event.target.value)}
                placeholder="Optional — issue number"
                data-testid="create-ticket-id"
              />
            </div>
          </Field>

          {create.error ? (
            <ErrorState
              className="max-w-none"
              title="Could not create the workspace"
              detail={create.error.message}
              retrying={create.isPending}
              onRetry={() => create.reset()}
            />
          ) : null}

      <DialogFooter showCloseButton>
        <Button type="submit" disabled={!ready || create.isPending} data-testid="create-submit">
          {create.isPending ? "Creating" : "Create workspace"}
        </Button>
      </DialogFooter>
    </form>
  );
}
