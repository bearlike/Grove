"use client";

import { LightbulbIcon } from "lucide-react";
import type { ReactNode } from "react";

import { CardShell } from "@/components/grove/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { LAUNCH_PRESETS } from "./launch-presets";
import { LAUNCH_TESTIDS } from "./launch-state";

/** Example briefs under the composer. "Try" fills the editor and never submits. */
export function LaunchSuggestions({
  onTry,
}: {
  readonly onTry: (prompt: string) => void;
}): ReactNode {
  return (
    <section
      className="launch-suggestions flex flex-col gap-3"
      aria-labelledby="launch-suggestions-heading"
      data-testid={LAUNCH_TESTIDS.suggestions}
    >
      <h2
        id="launch-suggestions-heading"
        className="launch-suggestions-heading text-content-secondary flex items-center gap-2 px-1 text-sm font-medium"
      >
        <LightbulbIcon className="size-[1em] shrink-0" aria-hidden />
        See what Grove can do
      </h2>
      <div className="flex flex-col gap-3 sm:flex-row">
        {LAUNCH_PRESETS.map(({ id, icon: Icon, tone, title, description, prompt }) => (
          <CardShell
            key={id}
            className="launch-suggestion min-h-44 min-w-0 flex-1 gap-2 p-4 pb-8"
            data-testid={`launch-suggestion-${id}`}
          >
            <div className="flex items-center justify-between">
              {/* The card's identity: its own hue. A coloured glyph reads larger
                  than a grey one, so this sits a step under the first cut. */}
              <Icon
                className={cn("size-6 shrink-0", tone)}
                aria-hidden
                data-slot="launch-suggestion-mark"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Try: ${title}`}
                onClick={() => onTry(prompt)}
                data-testid={`launch-try-${id}`}
              >
                Try
              </Button>
            </div>
            <p className="text-content-primary text-base font-semibold" data-slot="launch-suggestion-title">
              {title}
            </p>
            <p className="text-content-secondary text-base font-normal" data-slot="launch-suggestion-body">
              {description}
            </p>
          </CardShell>
        ))}
      </div>
    </section>
  );
}
