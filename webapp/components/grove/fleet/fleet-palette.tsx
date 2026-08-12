"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import { CommandPalette, type PaletteCommand } from "@/components/elements/command-palette";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { agentStateOf } from "./filter";
import type { FleetRow } from "./types";

/**
 * Jump to any workspace from anywhere, ⌘K / Ctrl-K.
 *
 * This is the counterpart to the sidebar's filter box, not a duplicate of it:
 * the filter narrows what you are looking at, the palette leaves the page you
 * are on. A fleet gets past the height of the sidebar long before it gets past
 * a person's memory of what they named things.
 */
export function FleetPalette({ rows }: { rows: readonly FleetRow[] }): React.ReactNode {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState("");

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key !== "k" || !(event.metaKey || event.ctrlKey)) return;
      event.preventDefault();
      setOpen((wasOpen) => !wasOpen);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const commands = useMemo<PaletteCommand[]>(
    () =>
      rows.map((row) => ({
        id: row.workspace.state.id,
        label: row.workspace.state.title,
        group: row.repoName,
        keys: [
          row.workspace.needs_attention ? "needs you" : agentStateOf(row.workspace),
        ],
      })),
    [rows],
  );

  const jump = (id: string): void => {
    setOpen(false);
    setQuery("");
    router.push(`/w/${id}`);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="p-0 sm:max-w-sm" showCloseButton={false}>
        <DialogHeader className="sr-only">
          <DialogTitle>Jump to a workspace</DialogTitle>
        </DialogHeader>
        <CommandPalette
          className="max-w-none"
          commands={commands}
          query={query}
          activeId={activeId || (commands[0]?.id ?? "")}
          onQueryChange={setQuery}
          onActiveChange={setActiveId}
          onRun={jump}
        />
      </DialogContent>
    </Dialog>
  );
}
