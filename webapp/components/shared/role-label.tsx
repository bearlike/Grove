import { SparklesIcon, UserIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * IRC-style speaker label for transcript surfaces — `you` for the human,
 * `agent` for the agent. The convention is shared with the TUI's transcript
 * (same words, same hues), so web and TUI read as siblings: agent rides the
 * agent-identity blue (`--ref-info`), you rides the clay accent (`--primary`,
 * the TUI's human-prompt hue). Branch teal was deliberately rejected for
 * `you` — the sessions tree renders branch names in `--ref-branch` teal on
 * the row directly above the digest, and the TUI hit the same collision.
 * Existing tokens only, micro-label tier (`text-[10px] uppercase
 * tracking-wider`, mono), matching the adjacent provenance label.
 *
 * A leading glyph is the transcript's one-per-concept vocabulary: `Sparkles`
 * is the agent/response mark everywhere the agent speaks, `User` the human.
 * Both inherit the label's color and are `aria-hidden` (the word carries the
 * meaning), so the label's `textContent` stays exactly `you` / `agent`.
 *
 * Test seam: `data-testid="role-label"` + `data-role-label`.
 */
export function RoleLabel({
  role,
  className,
}: {
  role: "you" | "agent";
  className?: string;
}) {
  const Glyph = role === "you" ? UserIcon : SparklesIcon;
  return (
    <span
      data-testid="role-label"
      data-role-label={role}
      className={cn(
        "inline-flex items-center gap-1 select-none align-middle font-mono text-[10px] font-semibold uppercase tracking-wider",
        role === "you" ? "text-primary" : "text-[var(--ref-info)]",
        className,
      )}
    >
      <Glyph aria-hidden className="size-3 shrink-0" />
      {role}
    </span>
  );
}
