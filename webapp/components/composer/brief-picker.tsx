"use client";

import { ChevronDown, Info } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PillTrigger } from "./pill";

const OPTIONS: { value: boolean | null; label: string }[] = [
  { value: null, label: "Default" },
  { value: true, label: "On" },
  { value: false, label: "Off" },
];

/**
 * The `Brief ▾` pill — mirrors `RuntimePicker`'s shape exactly. `null`
 * ("Default") cascades to the engine's `brief.enabled` config default, the
 * same wire convention `runtime`/`model`/`resume_session_id` already use for
 * "let the server decide". Controls whether the agent gets Grove's one-time
 * first-turn brief pointing it at the `working-in-grove` skill — a
 * create-time-only fact, never reachable from an edit surface.
 */
export function BriefPicker({
  value,
  onChange,
}: {
  value: boolean | null;
  onChange: (brief: boolean | null) => void;
}) {
  const selected = OPTIONS.find((o) => o.value === value) ?? OPTIONS[0];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <PillTrigger data-testid="composer-brief" aria-label={`Brief: ${selected.label}`}>
          <Info className="size-3.5" aria-hidden />
          <span className="truncate">{selected.label}</span>
          <ChevronDown className="size-3.5 opacity-60" aria-hidden />
        </PillTrigger>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>Brief</DropdownMenuLabel>
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
