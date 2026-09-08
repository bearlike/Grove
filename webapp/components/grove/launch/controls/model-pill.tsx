"use client";

import { useMemo, type ReactNode } from "react";

import { ModelMark, modelOptions } from "@/components/grove/model-option";
import { Input } from "@/components/ui/input";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { LAUNCH_TESTIDS, useLaunchControls } from "../launch-state";
import { customModelError } from "@/lib/grove/adapters/launch";
import { modelLabel } from "@/lib/grove/adapters/model";
import { useAgents, useModels, useWorkspaceDefaults } from "@/lib/grove/hooks";

// This cannot collide with a custom model id: the client validator and the wire
// contract both reject a leading dash. It is picker-only and never sent.
const CUSTOM_MODEL_OPTION = "-custom";
const CUSTOM_MODEL_MAX_LENGTH = 64;

/** The model resolved by the selected agent's catalog, or typed by the user. */
export function ModelPill(): ReactNode {
  const { values, set } = useLaunchControls();
  const agents = useAgents(values.repoRoot);
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const agentName = values.agentName ?? defaults.data?.agent ?? agents.data?.[0]?.name ?? null;
  const catalog = useModels(values.repoRoot, agentName);
  // A saved id that its agent no longer advertises is still an explicit custom
  // choice, just as the TUI selects MODEL_CUSTOM for it.
  const isCustom =
    values.customModel ||
    (values.model !== null && catalog.data !== undefined && !catalog.data.some(({ id }) => id === values.model));
  // The one place a catalog becomes rows, shared with the workspace composer
  // and the create dialog: label, brand mark and the full id as a search term.
  const { namespace, options: catalogOptions } = useMemo(
    () => modelOptions(catalog.data ?? []),
    [catalog.data],
  );
  const options = useMemo<readonly LaunchPillOption[]>(
    () => [
      ...catalogOptions.map(({ id, name, description, icon, keywords }) => ({
        id,
        label: name,
        description,
        icon,
        keywords,
      })),
      {
        id: CUSTOM_MODEL_OPTION,
        label: "Custom…",
        description: "Enter a provider model id",
        // Half an answer: the id itself is typed in the panel below, so the
        // menu must survive the click that reveals it.
        opensPanel: true,
      },
    ],
    [catalogOptions],
  );
  // NO `?? catalog[0]`. Same rule as the runtime pill: a control may only
  // display a value something actually resolved, and the first entry of a
  // config array is nobody's answer. With no saved model the request omits the
  // field, the engine appends no `--model`, and the tool picks its own default
  // — so naming `anthropic-deepseek-v4-pro` there (merely the first id in one
  // gateway's list) promised a model that would not be used. `null` is the
  // honest state and `fallbackLabel` names it.
  const selectedModel =
    isCustom ? values.model : values.model ?? defaults.data?.model ?? null;
  const validationError = isCustom ? customModelError(values.model ?? "") : null;
  const disabledReason =
    values.repoRoot === null
      ? "Choose a project first"
      : agentName !== null && !catalog.isPending && catalog.data?.length === 0
        ? `No models configured for ${agentName}`
        : undefined;

  return (
    <LaunchPill
      kind="model"
      ariaLabel="Model"
      value={isCustom ? CUSTOM_MODEL_OPTION : values.model}
      options={options}
      searchNoun="models"
      // "Agent default" is a VALUE, not a placeholder — it is precisely what
      // an omitted `model` produces, so it belongs here rather than the bare
      // control name a genuinely unknown value would get.
      fallbackLabel={selectedModel === null ? "Agent default" : modelLabel(selectedModel, namespace)}
      // The mark is the RESOLVED model's, so an unresolved control wears none:
      // a fixed cpu glyph beside "Agent default" would be decoration standing
      // where a value's own identity goes.
      leading={selectedModel ? <ModelMark id={selectedModel} /> : undefined}
      disabledReason={disabledReason}
      // The field is inside the popover now, so the sentence explaining a
      // disabled Send has to survive the menu closing over it.
      error={validationError}
      onSelect={(model) =>
        model === CUSTOM_MODEL_OPTION
          ? set({ model: "", customModel: true })
          : set({ model, customModel: false })
      }
    >
      {isCustom ? (
        // IN THE POPOVER, not beside the pills. On the row it was a text input
        // competing with five controls for one line — it took a whole line of
        // its own to avoid crushing them, and that line stayed on the composer
        // for as long as Custom was selected. A field belongs with the option
        // that asked for it, which is also the only place it reads as an
        // answer to the control rather than as a seventh control.
        <div className="flex flex-col gap-1">
          <label htmlFor={LAUNCH_TESTIDS.customModel} className="text-content-tertiary text-xs">
            Custom model id
          </label>
          <Input
            id={LAUNCH_TESTIDS.customModel}
            value={values.model ?? ""}
            onChange={(event) => set({ model: event.target.value, customModel: true })}
            placeholder="provider/model-id"
            maxLength={CUSTOM_MODEL_MAX_LENGTH}
            aria-label="Custom model id"
            aria-invalid={validationError !== null}
            aria-describedby={validationError ? LAUNCH_TESTIDS.customModelError : undefined}
            data-testid={LAUNCH_TESTIDS.customModel}
          />
          {validationError ? (
            <p
              id={LAUNCH_TESTIDS.customModelError}
              className="text-destructive text-xs"
              data-testid={LAUNCH_TESTIDS.customModelError}
            >
              {validationError}
            </p>
          ) : null}
        </div>
      ) : null}
    </LaunchPill>
  );
}
