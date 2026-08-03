"use client";

import { Box, ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Runtime } from "@/lib/grove/types";
import { PillTrigger } from "./pill";

const OPTIONS: { value: Runtime | null; label: string }[] = [
  { value: null, label: "Default" },
  { value: "host", label: "Host" },
  { value: "container", label: "Container" },
];

/**
 * The `Runtime ▾` pill — mirrors `AgentPicker`'s shape exactly. `null`
 * ("Default") cascades to the engine's `container.enabled` config default, the
 * same wire convention `model`/`resume_session_id` already use for "let the
 * server decide". Runtime is a create-time-only fact everywhere in Grove (CLI
 * `--runtime`, the TUI create Select, the MCP `runtime` param) — this is the
 * webapp's one control for it, never reachable from an edit surface.
 */
export function RuntimePicker({
  value,
  onChange,
}: {
  value: Runtime | null;
  onChange: (runtime: Runtime | null) => void;
}) {
  const selected = OPTIONS.find((o) => o.value === value) ?? OPTIONS[0];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <PillTrigger data-testid="composer-runtime" aria-label={`Runtime: ${selected.label}`}>
          <Box className="size-3.5" aria-hidden />
          <span className="truncate">{selected.label}</span>
          <ChevronDown className="size-3.5 opacity-60" aria-hidden />
        </PillTrigger>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>Runtime</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {OPTIONS.map((o) => (
          <DropdownMenuItem key={o.label} onSelect={() => onChange(o.value)}>
            <span className="truncate">{o.label}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
