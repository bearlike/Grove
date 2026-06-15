"use client";

import { ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AgentGlyph } from "@/lib/grove/agent-icon";
import type { AgentSummaryView } from "@/lib/grove/types";
import { PillTrigger } from "./pill";

/**
 * The `Agent ▾` pill — the composer's agent selector. A bordered rounded-full
 * DropdownMenu trigger showing the selected agent's brand glyph + name; the menu
 * lists every configured agent for the current repo (from `useAgents`). Purely
 * presentational: the container owns the list + the resolved value and hands us
 * `value`/`options`/`onChange`, so this leaf never fetches.
 */
export function AgentPicker({
  value,
  options,
  onChange,
}: {
  /** Selected agent name; null until the list resolves and the container defaults it. */
  value: string | null;
  options: AgentSummaryView[];
  onChange: (name: string) => void;
}) {
  const selected = options.find((a) => a.name === value) ?? null;
  const label = selected?.name ?? "Agent";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <PillTrigger data-testid="composer-agent" aria-label={`Agent: ${label}`}>
          {selected ? (
            <AgentGlyph
              agentName={selected.name}
              adapterKind={selected.kind}
              className="size-3.5"
            />
          ) : null}
          <span className="truncate">{label}</span>
          <ChevronDown className="size-3.5 opacity-60" aria-hidden />
        </PillTrigger>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
        <DropdownMenuLabel>Agent</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {options.length === 0 ? (
          <DropdownMenuItem disabled>No agents configured</DropdownMenuItem>
        ) : (
          options.map((a) => (
            <DropdownMenuItem key={a.name} onSelect={() => onChange(a.name)}>
              <AgentGlyph agentName={a.name} adapterKind={a.kind} className="size-3.5" />
              <span className="truncate">{a.name}</span>
              {a.description ? (
                <span className="ml-auto truncate text-xs text-muted-foreground">
                  {a.description}
                </span>
              ) : null}
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
