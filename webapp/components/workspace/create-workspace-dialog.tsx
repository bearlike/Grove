"use client";

import { useEffect, useMemo, useState } from "react";
import { LoaderCircle, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Textarea } from "@/components/ui/textarea";
import {
  useAgents,
  useBranches,
  useCreateWorkspace,
  useWorkspaces,
} from "@/lib/grove/hooks";
import type { BranchPlan, CreateWorkspaceRequest } from "@/lib/grove/types";
import { GroveProtocolError } from "@/lib/grove/client";

type BranchMode = BranchPlan["kind"];

const MODE_LABELS: Record<BranchMode, string> = {
  auto: "Auto — Grove generates from title + timestamp",
  new_named: "New — pick a name and base branch",
  existing_local: "Existing — check out a local branch",
  track_remote: "Remote — track a remote branch",
  root: "Root — work in the repo root (no worktree)",
};
const MODES = Object.keys(MODE_LABELS) as BranchMode[];

/**
 * The "New workspace" entry point + its create dialog — the webapp's mirror of
 * the TUI's create modal (`screens/create.py`). It speaks the SAME wire
 * contract: it builds a `BranchPlan` discriminated-union variant and submits a
 * `CreateWorkspaceRequest`. The five branch modes map 1:1 to the five union
 * variants; the engine validates everything (agent exists, branch resolves), so
 * the form stays thin and surfaces the typed refusal rather than re-checking.
 *
 * Repos come from the existing workspace list (the webapp is multi-repo and can
 * only offer projects it has seen) — unlike the TUI, which always launches from
 * inside one repo and knows its root.
 */
export function CreateWorkspaceButton() {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" data-testid="create-workspace-button">
          <Plus />
          <span className="hidden sm:inline">New workspace</span>
          <span className="sm:hidden">New</span>
        </Button>
      </DialogTrigger>
      <DialogContent
        data-testid="create-dialog"
        className="max-h-[90dvh] overflow-y-auto sm:max-w-xl"
      >
        <CreateWorkspaceForm onCreated={() => setOpen(false)} />
      </DialogContent>
    </Dialog>
  );
}

function CreateWorkspaceForm({ onCreated }: { onCreated: () => void }) {
  const { data: workspaces } = useWorkspaces();
  // Distinct repo roots the daemon already knows, newest workspaces first by
  // list order; basename is the human label, full path the value.
  const repos = useMemo(() => {
    const seen = new Map<string, string>();
    for (const w of workspaces ?? []) {
      if (!seen.has(w.repo_root)) {
        seen.set(w.repo_root, w.repo_root.split("/").filter(Boolean).pop() ?? w.repo_root);
      }
    }
    return [...seen.entries()].map(([root, name]) => ({ root, name }));
  }, [workspaces]);

  const [repoRoot, setRepoRoot] = useState<string | null>(null);
  // Default the repo to the first known one once the list resolves.
  useEffect(() => {
    if (repoRoot === null && repos.length > 0) setRepoRoot(repos[0].root);
  }, [repos, repoRoot]);

  const [agentName, setAgentName] = useState("");
  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<BranchMode>("auto");
  const [baseRef, setBaseRef] = useState("HEAD");
  const [newName, setNewName] = useState("");
  const [existingName, setExistingName] = useState("");
  const [remoteRef, setRemoteRef] = useState("");
  const [remoteLocal, setRemoteLocal] = useState("");
  const [initialPrompt, setInitialPrompt] = useState("");
  const [skipInit, setSkipInit] = useState(false);

  const agents = useAgents(repoRoot);
  // Local branches feed the base picker (auto/new) and the Existing picker; only
  // fetch remotes when the Remote mode actually needs them.
  const local = useBranches(repoRoot, "local", mode !== "track_remote");
  const remote = useBranches(repoRoot, "remote", mode === "track_remote");

  // Default the agent to the first configured one when the list resolves / the
  // repo changes (the current pick may not exist in the new repo's cascade).
  useEffect(() => {
    const list = agents.data;
    if (!list || list.length === 0) return;
    if (!list.some((a) => a.name === agentName)) setAgentName(list[0].name);
  }, [agents.data, agentName]);

  const baseOptions = useMemo(() => {
    const names = (local.data ?? []).map((b) => b.name);
    return ["HEAD", ...names.filter((n) => n !== "HEAD")];
  }, [local.data]);

  const create = useCreateWorkspace();

  function buildPlan(): BranchPlan {
    switch (mode) {
      case "new_named":
        return { kind: "new_named", name: newName.trim(), base_ref: baseRef };
      case "existing_local":
        return { kind: "existing_local", name: existingName };
      case "track_remote":
        return {
          kind: "track_remote",
          remote_ref: remoteRef,
          local_name: remoteLocal.trim() || null,
        };
      case "root":
        return { kind: "root" };
      case "auto":
      default:
        return { kind: "auto", base_ref: baseRef };
    }
  }

  // Client-side completeness only — the engine owns real validation. Disabling
  // submit when a mode's required field is empty avoids a guaranteed 422.
  const planReady =
    (mode === "auto" && true) ||
    (mode === "new_named" && newName.trim().length > 0) ||
    (mode === "existing_local" && existingName.length > 0) ||
    (mode === "track_remote" && remoteRef.length > 0) ||
    mode === "root";
  const canSubmit =
    Boolean(repoRoot) && agentName.length > 0 && title.trim().length > 0 && planReady;

  function onModeChange(next: BranchMode) {
    setMode(next);
    // Mirror the TUI: picking Root auto-checks skip-init (init scripts are built
    // for a fresh worktree, risky in the repo root). One-way nudge — switching
    // back never forces it off.
    if (next === "root") setSkipInit(true);
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !repoRoot) return;
    const req: CreateWorkspaceRequest = {
      agent_name: agentName,
      title: title.trim(),
      branch_plan: buildPlan(),
      skip_init: skipInit,
      initial_prompt: initialPrompt.trim() || null,
      repo_root: repoRoot,
    };
    create.mutate(req, { onSuccess: onCreated });
  }

  const error = create.error;
  const errorText =
    error instanceof GroveProtocolError
      ? error.message
      : error
        ? "Could not reach the daemon."
        : null;

  return (
    <form onSubmit={onSubmit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>New workspace</DialogTitle>
        <DialogDescription>
          Spin up an isolated worktree + agent session, just like the TUI.
        </DialogDescription>
      </DialogHeader>

      {repos.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="create-no-repos">
          No projects known yet. Create a workspace from the TUI or CLI once, then
          this dashboard can drive the rest.
        </p>
      ) : (
        <>
          <Field label="Project" htmlFor="create-repo">
            <NativeSelect
              id="create-repo"
              data-testid="create-repo"
              value={repoRoot ?? ""}
              onChange={(e) => setRepoRoot(e.target.value)}
            >
              {repos.map((r) => (
                <option key={r.root} value={r.root} title={r.root}>
                  {r.name}
                </option>
              ))}
            </NativeSelect>
          </Field>

          <Field label="Agent" htmlFor="create-agent">
            <NativeSelect
              id="create-agent"
              data-testid="create-agent"
              value={agentName}
              onChange={(e) => setAgentName(e.target.value)}
            >
              {(agents.data ?? []).map((a) => (
                <option key={a.name} value={a.name}>
                  {a.description ? `${a.name} — ${a.description}` : a.name}
                </option>
              ))}
            </NativeSelect>
          </Field>

          <Field label="Title" htmlFor="create-title">
            <Input
              id="create-title"
              data-testid="create-title"
              placeholder="my-task"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              autoFocus
            />
          </Field>

          <Field label="Branch" htmlFor="create-mode">
            <NativeSelect
              id="create-mode"
              data-testid="create-mode"
              value={mode}
              onChange={(e) => onModeChange(e.target.value as BranchMode)}
            >
              {MODES.map((m) => (
                <option key={m} value={m}>
                  {MODE_LABELS[m]}
                </option>
              ))}
            </NativeSelect>
          </Field>

          {/* Variant-specific inputs — the discriminated union's per-kind fields. */}
          {(mode === "auto" || mode === "new_named") && (
            <BranchModeFields>
              {mode === "new_named" && (
                <Field label="Branch name" htmlFor="create-new-name">
                  <Input
                    id="create-new-name"
                    data-testid="create-new-name"
                    placeholder="feature/payment-v2"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                  />
                </Field>
              )}
              <Field label="Base ref" htmlFor="create-base">
                <NativeSelect
                  id="create-base"
                  data-testid="create-base"
                  value={baseRef}
                  onChange={(e) => setBaseRef(e.target.value)}
                >
                  {baseOptions.map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            </BranchModeFields>
          )}

          {mode === "existing_local" && (
            <BranchModeFields>
              <Field label="Local branch" htmlFor="create-existing">
                <NativeSelect
                  id="create-existing"
                  data-testid="create-existing"
                  value={existingName}
                  onChange={(e) => setExistingName(e.target.value)}
                >
                  <option value="" disabled>
                    {local.data && local.data.length > 0 ? "Pick a branch…" : "No local branches"}
                  </option>
                  {(local.data ?? []).map((b) => (
                    <option key={b.name} value={b.name}>
                      {b.name}
                      {b.is_current ? " (current)" : ""}
                      {b.checked_out_in ? " (checked out)" : ""}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            </BranchModeFields>
          )}

          {mode === "track_remote" && (
            <BranchModeFields>
              <Field label="Remote branch" htmlFor="create-remote-ref">
                <NativeSelect
                  id="create-remote-ref"
                  data-testid="create-remote-ref"
                  value={remoteRef}
                  onChange={(e) => setRemoteRef(e.target.value)}
                >
                  <option value="" disabled>
                    {remote.data && remote.data.length > 0
                      ? "Pick a remote branch…"
                      : "No remote branches (git fetch first?)"}
                  </option>
                  {(remote.data ?? []).map((b) => (
                    <option key={b.name} value={b.name}>
                      {b.name}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Local name (optional)" htmlFor="create-remote-local">
                <Input
                  id="create-remote-local"
                  data-testid="create-remote-local"
                  placeholder="<derived from remote>"
                  value={remoteLocal}
                  onChange={(e) => setRemoteLocal(e.target.value)}
                />
              </Field>
            </BranchModeFields>
          )}

          {mode === "root" && (
            <p
              className="rounded-md border border-border bg-muted/40 p-3 text-xs text-muted-foreground"
              data-testid="create-root-note"
            >
              Runs in the repo root on the current branch. No worktree is created;
              pause and resume do not apply (use kill to stop, respawn to restart).
            </p>
          )}

          <Field label="Initial prompt (optional)" htmlFor="create-initial-prompt">
            <Textarea
              id="create-initial-prompt"
              data-testid="create-initial-prompt"
              placeholder="The agent's first task — delivered as the session boots."
              value={initialPrompt}
              onChange={(e) => setInitialPrompt(e.target.value)}
            />
          </Field>

          <label className="flex items-center gap-2 text-sm" htmlFor="create-skip-init">
            <input
              id="create-skip-init"
              data-testid="create-skip-init"
              type="checkbox"
              className="size-4 rounded border-input accent-primary"
              checked={skipInit}
              onChange={(e) => setSkipInit(e.target.checked)}
            />
            Skip init script
          </label>

          {errorText && (
            <p
              role="alert"
              data-testid="create-error"
              className="rounded-md border border-[var(--status-error)]/40 bg-[var(--status-error)]/10 p-2 text-sm text-foreground"
            >
              {errorText}
            </p>
          )}
        </>
      )}

      <DialogFooter>
        <Button
          type="submit"
          data-testid="create-submit"
          disabled={!canSubmit || create.isPending}
        >
          {create.isPending && <LoaderCircle className="animate-spin" />}
          Create workspace
        </Button>
      </DialogFooter>
    </form>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

function BranchModeFields({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid gap-4 rounded-md border border-border bg-muted/30 p-3">{children}</div>
  );
}
