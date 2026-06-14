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
 * Test seam: `data-testid="role-label"` + `data-role-label`.
 */
export function RoleLabel({
  role,
  className,
}: {
  role: "you" | "agent";
  className?: string;
}) {
  return (
    <span
      data-testid="role-label"
      data-role-label={role}
      className={cn(
        "select-none font-mono text-[10px] font-semibold uppercase tracking-wider",
        role === "you" ? "text-primary" : "text-[var(--ref-info)]",
        className,
      )}
    >
      {role}
    </span>
  );
}
