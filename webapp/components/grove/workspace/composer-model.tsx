"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  ModelSelector,
  type ModelOption,
} from "@/components/assistant-ui/model-selector";
import { ModelMark, enrichedCatalog, modelOptions } from "@/components/grove/model-option";
import { modelLabel } from "@/lib/grove/adapters/model";
import { useModels, useSessionControls, useSwitchModel, useWorkspace } from "@/lib/grove/hooks";

type RequestedModel = {
  id: string;
  reportedModel: string | null;
};

/**
 * The running session's model control, composed into the workspace composer.
 *
 * `models` is the agent's switch vocabulary while `current_model` is a provider
 * report from the transcript, so only an exact catalog match selects a row.
 * Otherwise a delivered request is the choice the reader made in this menu,
 * and it stays pending only until the provider reports a changed model value.
 */
export function ComposerModel({
  workspaceId,
}: {
  readonly workspaceId: string;
}): React.ReactNode {
  const [open, setOpen] = useState(false);
  const [requested, setRequested] = useState<RequestedModel | null>(null);
  const controls = useSessionControls(workspaceId);
  const switchModel = useSwitchModel(workspaceId);
  const currentModel = controls.data?.current_model ?? null;
  const ids = controls.data?.models ?? [];
  // MEMBERSHIP stays the live session's own switch vocabulary; the catalog
  // supplies only the name and window per row. Swapping the list for the
  // catalog's would offer the agent a model it cannot switch to, and drop one
  // it can — the two answer different questions about the same ids.
  const workspace = useWorkspace(workspaceId);
  const catalog = useModels(
    workspace.data?.repo_root ?? null,
    workspace.data?.agent_name ?? null,
  );
  const entries = useMemo(
    () => enrichedCatalog(ids, catalog.data),
    [ids, catalog.data],
  );
  const { namespace, options } = useMemo(() => modelOptions(entries), [entries]);
  // A delivery in flight disables every row rather than the control, so the
  // menu still reads while it cannot act.
  const models = useMemo<readonly ModelOption[]>(
    () => options.map((option) => ({ ...option, disabled: switchModel.isPending })),
    [options, switchModel.isPending],
  );
  // One precedence, read twice: as text for the trigger, and as an id whenever
  // either half is present. Only an id gets a brand mark and the namespace fold
  // — "Agent default" is the VALUE an omitted `--model` produces, not a name.
  const label = requested?.id ?? currentModel ?? "Agent default";
  const labelId = requested?.id ?? currentModel;
  // The trigger prints the canonical name when the catalog declares one for
  // this exact id. `current_model` is a PROVIDER report and lives in a
  // different namespace from the switch catalog (measured: 9 matches in ~10,900
  // real assistant messages), so this usually misses and falls back to the id's
  // own spelling — which is correct, because the agent said that word.
  const declaredName = labelId
    ? (catalog.data?.find((option) => option.id === labelId)?.name ?? null)
    : null;

  useEffect(() => {
    if (requested && currentModel !== requested.reportedModel) setRequested(null);
  }, [currentModel, requested]);

  return (
    // The toolbar is now one native action group, so this control contributes a
    // trigger and a pending mark and nothing else. The permission mode moved
    // INTO the menu (below): on the row it was a lone lowercase word beside two
    // controls, which reads as decoration rather than as a reported fact.
    <div className="flex min-w-0 items-center gap-1">
      <ModelSelector.Root
        models={models}
        value={requested?.id ?? (currentModel && ids.includes(currentModel) ? currentModel : "")}
        open={open}
        onOpenChange={setOpen}
        onValueChange={(id) => {
          switchModel.mutate(id, {
            onSuccess: () => {
              setRequested({ id, reportedModel: currentModel });
              toast.success("Model request delivered", {
                description: "Waiting for the agent to report its model.",
              });
            },
            onError: (error) => {
              toast.error("Couldn’t deliver model request", {
                description: error.message,
              });
            },
          });
        }}
      >
        {/* Trigger appends its own chevron, so `asChild` would give Radix two
            children and crash the page. Compose the selector's trigger directly. */}
        <ModelSelector.Trigger
          variant="outline"
          size="sm"
          disabled={controls.isPending}
          aria-label="Model"
          title={
            requested
              ? "Requested model; waiting for the agent report"
              : currentModel
                ? `Reported current model: ${currentModel}`
                : "The agent has not reported its current model"
          }
          className="min-h-[24px] max-w-[240px]"
          data-testid="composer-model-trigger"
        >
          {labelId && <ModelMark id={labelId} className="size-3.5" />}
          <span className="truncate">
            {labelId ? (declaredName ?? modelLabel(labelId, namespace)) : label}
          </span>
        </ModelSelector.Trigger>
        {requested && (
          <span
            aria-label="Requested model; waiting for the agent report"
            title="Requested model; waiting for the agent report"
            className="size-[6px] shrink-0 bg-primary"
            data-slot="status-dot"
            data-testid="composer-model-pending"
          />
        )}
        <ModelSelector.Content
          align="start"
          searchable
          data-testid="composer-model-menu"
          aria-label="Choose model"
        >
          <ModelSelector.Search />
          {/* This menu contains choices for the live session; its trigger alone
              names the omitted-model fallback. */}
          {controls.data?.permission_mode && (
            <div
              className="flex items-center justify-between gap-2 px-2 py-1 text-content-tertiary"
              title="The permission mode this session prompts with by default"
              data-testid="composer-permission-mode"
            >
              <span>Permission mode</span>
              <span className="truncate">{controls.data.permission_mode}</span>
            </div>
          )}
          <ModelSelector.List>
            <ModelSelector.Empty />
            <ModelSelector.Group heading={namespace || undefined}>
              {models.map((model) => (
                // The 26px fixed height this row used to carry was sized for a
                // one-line label; a row now holds a name over its context
                // window, so pinning it crushed two lines into one line's
                // space. The vendored row sizes itself — let it.
                <ModelSelector.Item
                  key={model.id}
                  model={model}
                  title={model.id}
                  data-testid="composer-model-item"
                />
              ))}
            </ModelSelector.Group>
          </ModelSelector.List>
        </ModelSelector.Content>
      </ModelSelector.Root>
    </div>
  );
}
