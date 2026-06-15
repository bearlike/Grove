"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import type { AgentKind, ModelOption } from "./models";
import { MODELS_BY_KIND } from "./models";
import { PillTrigger } from "./pill";

/**
 * The `Model ▾` pill — only rendered by the container when the selected agent's
 * kind has a launch-time `--model` flag (claude_code / codex). The menu offers a
 * "Default" row (model=null, the tool's own default) at the top, the kind's
 * well-known ids/aliases in the middle, and a "Custom model id…" free-text row
 * pinned at the bottom — the always-available escape hatch. The custom row is an
 * <Input> embedded in the menu; we stop key/selection propagation so typing
 * doesn't drive menu navigation or dismiss the menu.
 */
export function ModelPicker({
  value,
  kind,
  onChange,
}: {
  /** Selected model id; null = the adapter's default. */
  value: string | null;
  kind: AgentKind;
  onChange: (model: string | null) => void;
}) {
  const options: readonly ModelOption[] = MODELS_BY_KIND[kind] ?? [];
  const known = options.find((o) => o.value === value);
  // A value that isn't null and isn't a known option is a custom id the user typed.
  const isCustom = value !== null && known === undefined;
  const triggerLabel = value === null ? "Default model" : (known?.label ?? value);

  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");

  function commitCustom() {
    const next = draft.trim();
    if (next.length === 0) return;
    onChange(next);
    setDraft("");
    setOpen(false); // commit closes the menu (also clears radix's body pointer-lock).
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <PillTrigger data-testid="composer-model" aria-label={`Model: ${triggerLabel}`}>
          <span className="truncate">{triggerLabel}</span>
          <ChevronDown className="size-3.5 opacity-60" aria-hidden />
        </PillTrigger>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56">
        <DropdownMenuLabel>Model</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          data-testid="composer-model-default"
          onSelect={() => onChange(null)}
        >
          Default
          {value === null ? (
            <span className="ml-auto text-xs text-muted-foreground">tool default</span>
          ) : null}
        </DropdownMenuItem>
        {options.map((o) => (
          <DropdownMenuItem key={o.value} onSelect={() => onChange(o.value)}>
            {o.label}
            {value === o.value ? (
              <span className="ml-auto text-xs text-muted-foreground">selected</span>
            ) : null}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        {/* Free-text custom id — typing must not trigger menu nav/close. */}
        <div
          className="px-2 py-1.5"
          // Radix DropdownMenu treats typeahead/Enter on items; keep keystrokes
          // local to the input so the menu doesn't intercept them.
          onKeyDown={(e) => e.stopPropagation()}
        >
          <label
            className="mb-1 block text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground"
            htmlFor="composer-model-custom"
          >
            Custom model id
          </label>
          <Input
            id="composer-model-custom"
            data-testid="composer-model-custom"
            placeholder={isCustom ? value : "e.g. claude-opus-4-8"}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                commitCustom();
              }
            }}
            className="h-7 text-xs"
          />
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
