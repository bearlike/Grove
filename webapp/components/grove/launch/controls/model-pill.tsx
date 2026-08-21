"use client";

import { useMemo, type ReactNode } from "react";

import { Input } from "@/components/ui/input";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { LAUNCH_TESTIDS, useLaunchControls } from "../launch-state";
import { customModelError } from "@/lib/grove/adapters/launch";
import { useWorkspaceDefaults } from "@/lib/grove/hooks/launch";
import { useAgents } from "@/lib/grove/hooks/queries";

// This cannot collide with a custom model id: the client validator and the wire
// contract both reject a leading dash. It is picker-only and never sent.
const CUSTOM_MODEL_OPTION = "-custom";
const CUSTOM_MODEL_MAX_LENGTH = 64;

/**
 * Capitalize only all-letter words separated by spaces or dashes; provider ids
 * with digits, dots, or slashes remain verbatim. This label is display-only:
 * catalog ids stay opaque and are forwarded to the provider unchanged.
 */
export function modelLabel(model: string): string {
  if (!/^[a-z]+(?:[- ][a-z]+)*$/.test(model)) return model;
  return model.replace(/(^|[- ])([a-z])/g, (_, separator: string, letter: string) =>
    `${separator}${letter.toUpperCase()}`,
  );
}

/** The model resolved by the selected agent's catalog, or typed by the user. */
export function ModelPill(): ReactNode {
  const { values, set } = useLaunchControls();
  const agents = useAgents(values.repoRoot);
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const agentName = values.agentName ?? defaults.data?.agent ?? agents.data?.[0]?.name ?? null;
  const agent = agents.data?.find((entry) => entry.name === agentName);
  const catalog = agent?.models ?? [];
  // A saved id that its agent no longer advertises is still an explicit custom
  // choice, just as the TUI selects MODEL_CUSTOM for it.
  const isCustom =
    values.customModel ||
    (values.model !== null && agent !== undefined && !catalog.includes(values.model));
  const options = useMemo<readonly LaunchPillOption[]>(
    () => [
      ...catalog.map((model) => ({ id: model, label: modelLabel(model) })),
      {
        id: CUSTOM_MODEL_OPTION,
        label: "Custom…",
        description: "Enter a provider model id",
      },
    ],
    [catalog],
  );
  const selectedModel =
    isCustom ? values.model : values.model ?? defaults.data?.model ?? catalog[0] ?? null;
  const validationError = isCustom ? customModelError(values.model ?? "") : null;
  const disabledReason =
    values.repoRoot === null
      ? "Choose a project first"
      : agentName !== null && !agents.isPending && catalog.length === 0
        ? `No models configured for ${agentName}`
        : undefined;

  return (
    <>
      <LaunchPill
        kind="model"
        ariaLabel="Model"
        value={isCustom ? CUSTOM_MODEL_OPTION : values.model}
        options={options}
        fallbackLabel={selectedModel === null ? "Model" : modelLabel(selectedModel)}
        disabledReason={disabledReason}
        onSelect={(model) =>
          model === CUSTOM_MODEL_OPTION
            ? set({ model: "", customModel: true })
            : set({ model, customModel: false })
        }
      />
      {isCustom ? (
        // `basis-full` puts the field on its OWN line of the wrapping control
        // row. Inline it was a text input competing with six pills for one
        // line, which crushed every label beside it the moment Custom was
        // picked — the state in which the row is least able to spare the width.
        <div className="flex basis-full flex-col gap-1">
          <Input
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
              className="text-xs text-destructive"
              data-testid={LAUNCH_TESTIDS.customModelError}
            >
              {validationError}
            </p>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
