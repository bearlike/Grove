import catalog from "./tool-catalog.json";

export interface ToolPresentation {
  verb: string;
  chip: string;
  icon: string;
  kind: "tool" | "read" | "command" | "edit";
  filePath: string | null;
  /** Present only for an edit whose diff Grove actually computed. The counts
   * are the diff's, never a guess from the tool name — an `Edit` call whose
   * payload the provider did not report contributes a step and no chip. */
  fileStat?: { file: string; added: number; removed: number };
}

/** One file's total change across every edit to it in a run. */
export interface ToolTimelineStat {
  file: string;
  added: number;
  removed: number;
}

interface CatalogEntry {
  names: string[];
  verb: string;
  icon: string;
  kind?: ToolPresentation["kind"];
  target?: string[];
}

const entries = new Map<string, CatalogEntry>(
  (catalog.tools as CatalogEntry[]).flatMap(entry => entry.names.map(name => [name, entry] as const)),
);

/** Provider namespaces are protocol, not arbitrary suffix matching: MCP Read is not builtin Read. */
export function toolPresentation(
  name: string,
  input: Readonly<Record<string, unknown>> | null = null,
  digest = "",
  serverIcons: Readonly<Record<string, string>> = {},
): ToolPresentation {
  const mcp = name.match(/^(?:functions\.)?mcp__([^]+?)__(.+)$/);
  if (mcp) {
    const [, server, tool] = mcp;
    const icon = Object.hasOwn(serverIcons, server) ? serverIcons[server] : "flat-color-icons:services";
    return { verb: "Called", chip: `${server} / ${tool}`, icon, kind: "tool", filePath: null };
  }
  const bareName = name.replace(/^(?:functions|multi_tool_use)\./, "");
  const entry = entries.get(bareName);
  if (!entry) {
    return { verb: "Called", chip: digest ? `${name} · ${digest}` : name, icon: "flat-color-icons:settings", kind: "tool", filePath: null };
  }
  let target: string | null = null;
  for (const key of entry.target ?? []) {
    const value = input?.[key];
    if (typeof value === "string" && value.trim()) { target = value; break; }
    if (typeof value === "number") { target = String(value); break; }
    if (Array.isArray(value) && value.length > 0 && value.every(item => typeof item === "string")) {
      target = JSON.stringify(value);
      break;
    }
  }
  return {
    verb: entry.verb,
    chip: target ?? (digest || bareName),
    icon: entry.icon,
    kind: entry.kind ?? "tool",
    filePath: entry.kind === "read" ? target : null,
  };
}

/** A display-only preview, never a shell parser or a replacement for the recorded input. */
export function compactToolTarget(step: ToolPresentation): string {
  let preview = step.chip.trim();
  if (step.kind === "command") {
    let words = preview.split(/\s+/);
    if (preview.startsWith("[")) {
      try {
        const argv: unknown = JSON.parse(preview);
        if (Array.isArray(argv) && argv.every(value => typeof value === "string")) words = argv;
      } catch { /* A shell expression beginning with [ is not necessarily JSON. */ }
    }
    preview = words.slice(0, 2).join(" ");
    if (words.length > 2) preview += " …";
  } else if (step.kind === "read" || step.kind === "edit") {
    preview = preview.split(/[\\/]/).filter(Boolean).at(-1) ?? preview;
  }
  preview = preview.replace(/\s+/g, " ");
  return preview.length > 48 ? `${preview.slice(0, 47).trimEnd()}…` : preview;
}

/**
 * Per-file change totals for the timeline's trailing chips.
 *
 * Keyed on the FULL path and displayed as the basename: two `app.ts` files in
 * different directories are two rows that happen to print the same word, and
 * merging them would report a change count for a file nobody edited. Insertion
 * order is preserved, so the chips read in the order the run touched them.
 */
export function toolTimelineStats(steps: readonly ToolPresentation[]): ToolTimelineStat[] {
  const totals = new Map<string, ToolTimelineStat>();
  for (const step of steps) {
    if (!step.fileStat) continue;
    const held = totals.get(step.fileStat.file);
    if (held) {
      held.added += step.fileStat.added;
      held.removed += step.fileStat.removed;
      continue;
    }
    totals.set(step.fileStat.file, {
      file: step.fileStat.file.split(/[\\/]/).filter(Boolean).at(-1) ?? step.fileStat.file,
      added: step.fileStat.added,
      removed: step.fileStat.removed,
    });
  }
  return [...totals.values()];
}

/** Count observed read paths, never files guessed from a shell command or a search's output. */
export function toolTimelineSummary(steps: readonly ToolPresentation[]): { label: string; icons: string[] } {
  // A read path and an edit path are different claims about the same file, so
  // they are counted separately: "2 files read · 1 file changed" states two
  // facts, and folding them into one number would state neither.
  const files = new Set(
    steps.flatMap(step => (step.kind === "read" && step.filePath !== null ? [step.filePath] : [])),
  );
  const changed = toolTimelineStats(steps).length;
  const commands = steps.filter(step => step.kind === "command").length;
  const counts = [`${steps.length} ${steps.length === 1 ? "step" : "steps"}`];
  if (commands > 0) counts.push(`${commands} ${commands === 1 ? "command" : "commands"}`);
  if (files.size > 0) counts.push(`${files.size} ${files.size === 1 ? "file" : "files"} read`);
  if (changed > 0) counts.push(`${changed} ${changed === 1 ? "file" : "files"} changed`);
  const first = steps[0];
  // WITH NO CATEGORY TO REPORT, THE STEP TALLY IS THE WHOLE CLAIM, so it is
  // said as a sentence rather than as a bare noun. `N tool calls` restated the
  // number the step count already carries and named the mechanism instead of
  // the work; `N steps executed` closes the phrase, which is what stops the
  // summary reading like a count whose other half went missing. A run of ONE
  // still prefers its own action ("Loaded skill verification") — that is
  // strictly more than a tally, and it is the only case where we have it.
  const activity = steps.length === 1 && first
    ? `${first.verb} ${compactToolTarget(first)}`.trim()
    : steps.length > 1 ? `${steps.length} steps executed` : "Tool activity";
  return {
    label: counts.length > 1 ? counts.join(" · ") : activity,
    icons: [...new Set(steps.map(step => step.icon))],
  };
}
