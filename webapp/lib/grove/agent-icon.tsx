import { Bot, Terminal, type LucideIcon } from "lucide-react";
import { siAnthropic, siClaude, type SimpleIcon } from "simple-icons";

/**
 * Agent → icon abstraction. The extensible seam that mirrors the backend's
 * `AgentAdapter` registry: a card asks "which glyph represents this agent?" and
 * gets back either a brand mark (simple-icons path) or a lucide fallback.
 *
 * The fallback exists because OpenAI / Codex / OpenCode / Gemini have NO
 * off-the-shelf brand glyph in either simple-icons or lucide-react (verified
 * 2026-06-08 — `siOpenai` does not exist). lucide `Bot` / `Terminal` stand in
 * until a real brand glyph ships; adding one is a one-line edit to AGENT_ICON.
 */

export type SimpleIconData = SimpleIcon;

/**
 * Render a simple-icons brand mark. Color comes from `currentColor` so a
 * `text-*` utility on the caller drives the tint (we want the glyph to inherit
 * the activity-tier color, not lock to the brand hex). `hex` is still exported
 * via AGENT_ICON for callers that want to opt into the brand color.
 */
export function SimpleIcon({
  icon,
  className = "size-4",
  title,
}: {
  icon: SimpleIconData;
  className?: string;
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <path d={icon.path} />
    </svg>
  );
}

/**
 * One registry entry. `brand` carries a simple-icons object; `lucide` carries a
 * lucide component. `label` is the human agent name for aria/title.
 */
type AgentIconEntry =
  | { kind: "brand"; icon: SimpleIconData; label: string }
  | { kind: "lucide"; Icon: LucideIcon; label: string };

/**
 * The extensible registry. Keys are matched first as an `adapter_kind` (exact),
 * then as a case-insensitive substring of the agent name. Plain `as const`
 * object on purpose: adding an agent — or swapping a lucide fallback for a real
 * brand glyph once one lands in simple-icons — is a one-line change here.
 */
export const AGENT_ICON = {
  claude: { kind: "brand", icon: siClaude, label: "Claude Code" },
  claude_code: { kind: "brand", icon: siClaude, label: "Claude Code" },
  anthropic: { kind: "brand", icon: siAnthropic, label: "Anthropic" },
  // No brand glyph in simple-icons/lucide for these (verified 2026-06-08) —
  // lucide fallback is the intended extensibility point, not a placeholder bug.
  codex: { kind: "lucide", Icon: Bot, label: "Codex" },
  opencode: { kind: "lucide", Icon: Terminal, label: "OpenCode" },
  openai: { kind: "lucide", Icon: Bot, label: "OpenAI" },
  gpt: { kind: "lucide", Icon: Bot, label: "GPT" },
  gemini: { kind: "lucide", Icon: Bot, label: "Gemini" },
} as const satisfies Record<string, AgentIconEntry>;

const DEFAULT_ENTRY: AgentIconEntry = { kind: "lucide", Icon: Bot, label: "Agent" };

/**
 * Resolve an agent to its icon. `adapter_kind` (the backend's canonical agent
 * identity) wins; the agent name is a softer fallback so a bare "claude-sonnet"
 * still resolves to the Claude mark. Unknown agents get the generic Bot.
 */
export function resolveAgentIcon(
  agentName: string,
  adapterKind?: string,
):
  | { kind: "brand"; icon: SimpleIconData; hex: string; label: string }
  | { kind: "lucide"; Icon: LucideIcon; label: string } {
  const entry = lookup(agentName, adapterKind);
  if (entry.kind === "brand") {
    return { kind: "brand", icon: entry.icon, hex: `#${entry.icon.hex}`, label: entry.label };
  }
  return { kind: "lucide", Icon: entry.Icon, label: entry.label };
}

function lookup(agentName: string, adapterKind?: string): AgentIconEntry {
  if (adapterKind && adapterKind in AGENT_ICON) {
    return AGENT_ICON[adapterKind as keyof typeof AGENT_ICON];
  }
  const name = agentName.toLowerCase();
  for (const key of Object.keys(AGENT_ICON) as (keyof typeof AGENT_ICON)[]) {
    if (name.includes(key)) return AGENT_ICON[key];
  }
  return DEFAULT_ENTRY;
}

/**
 * Render the resolved agent icon statically (brand → SimpleIcon, lucide → the
 * component). Color inherits from the caller's `text-*`; default size-4.
 */
export function AgentGlyph({
  agentName,
  adapterKind,
  className = "size-4",
}: {
  agentName: string;
  adapterKind?: string;
  className?: string;
}) {
  const resolved = resolveAgentIcon(agentName, adapterKind);
  if (resolved.kind === "brand") {
    return <SimpleIcon icon={resolved.icon} className={className} title={resolved.label} />;
  }
  const { Icon, label } = resolved;
  return <Icon className={className} role="img" aria-label={label} />;
}
