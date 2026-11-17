"use client";

import { useMemo, type ReactNode } from "react";

import { runtimeGlyph, runtimeLabel } from "@/components/grove/fleet/tokens";
import { LaunchPill, type LaunchPillOption } from "../control-pill";
import { type RuntimeChoice, useLaunchControls, useLaunchProblem } from "../launch-state";
import {
  RUNTIME_GROUP_LABEL,
  runtimeKey,
  runtimeKindOf,
  runtimeRows,
} from "@/lib/grove/adapters/launch-flow";
import { useLaunchOptions, useWorkspaceDefaults } from "@/lib/grove/hooks/launch";

/** A choice's label: the runtime's word, or the remote block's own name. */
function choiceLabel(choice: RuntimeChoice): string {
  const kind = runtimeKindOf(choice);
  return kind === "remote" ? choice : runtimeLabel(kind);
}

/** The engine's reason, as a sentence: it reads `docker: not found on PATH`. */
function sentence(text: string): string {
  const trimmed = text.trim();
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

/**
 * The few words a disabled row shows: the engine's reason before its remedy.
 * The remedy (an install command, a config block) is the row's hover detail,
 * so the menu reads as a list rather than a manual. A one-word lead such as
 * `docker:` is a label for what follows, so it keeps the clause it names.
 */
export function runtimeHeadline(text: string): string {
  const first = sentence(text).split(/ — |\. /)[0]!;
  const parts = first.split(": ");
  const head = parts[0]!.includes(" ") || parts.length === 1 ? parts[0]! : parts.slice(0, 2).join(": ");
  return head.replace(/[.:]$/, "");
}

/**
 * Where the agent runs, from the engine's own inventory (`GET /launch/options`).
 *
 * Every row is the engine's, grouped Available → Unavailable here → Not set up
 * so what can be picked is never below what cannot: configured runtimes are
 * selectable, implemented ones nobody configured are listed but disabled with
 * their setup step, and episodic task-only blocks never appear because they
 * cannot be a workspace. Every name is a human one (`runtimeOptionLabel`).
 * The rows are the selected node's (the node pill chooses the node, including
 * "any"), so a hub's remote node never offers this daemon's runtimes.
 *
 * NO `?? "host"` FALLBACK, for the reason the guide records: an untouched pill
 * shows the engine's resolved answer, and nothing before that arrives.
 */
export function RuntimePill(): ReactNode {
  const { values, set } = useLaunchControls();
  const problem = useLaunchProblem("runtime");
  const defaults = useWorkspaceDefaults(values.repoRoot);
  const inventory = useLaunchOptions(values.repoRoot);
  const rows = useMemo(
    () =>
      runtimeRows(inventory.data?.runtimes ?? [], values.node, runtimeLabel),
    [inventory.data?.runtimes, values.node],
  );
  const options = useMemo<readonly LaunchPillOption[]>(
    () =>
      rows.map((row) => {
        const Glyph = runtimeGlyph(row.kind);
        return {
          id: row.key,
          label: row.label,
          group: RUNTIME_GROUP_LABEL[row.group],
          icon: (
            <span data-runtime-kind={row.kind}>
              <Glyph aria-hidden />
            </span>
          ),
          ...(row.unavailable !== null
            ? {
                disabled: true,
                // A "Not set up" row says nothing its heading does not; how to
                // set it up is the hover detail.
                ...(row.group === "setup" ? {} : { description: runtimeHeadline(row.unavailable) }),
                detail: sentence(row.unavailable),
              }
            : row.note !== null
              ? { description: row.note }
              : {}),
        };
      }),
    [rows],
  );
  const selectedRuntime = values.runtime ?? defaults.data?.runtime ?? null;
  const ActiveGlyph = selectedRuntime === null ? null : runtimeGlyph(runtimeKindOf(selectedRuntime));

  return (
    <LaunchPill
      kind="runtime"
      ariaLabel="Runtime"
      value={values.runtime === null ? null : runtimeKey(values.runtime, values.node)}
      options={options}
      searchNoun="runtimes"
      fallbackLabel={selectedRuntime === null ? "Runtime" : choiceLabel(selectedRuntime)}
      disabledReason={values.repoRoot === null ? "Choose a project first" : undefined}
      leading={
        ActiveGlyph === null || selectedRuntime === null ? undefined : (
          <span data-runtime-kind={runtimeKindOf(selectedRuntime)}>
            <ActiveGlyph aria-hidden />
          </span>
        )
      }
      error={problem}
      onSelect={(key) => {
        const row = rows.find((candidate) => candidate.key === key);
        if (row) set({ runtime: row.runtime });
      }}
    />
  );
}
