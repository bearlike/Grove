import type { MailboxContact, SessionControlView } from "@/lib/grove/api";

/**
 * What the workspace composer's `/` and `@` triggers offer, as pure data.
 *
 * The shapes are assistant-ui's own trigger vocabulary (`Unstable_SlashCommand`
 * without its `execute`, `Unstable_Mention`) so the component maps them onto
 * `unstable_useSlashCommandAdapter` / `unstable_useMentionAdapter` verbatim.
 * They live here, free of React and of `groveClient`, so the rules are tested
 * without a runtime.
 */

/**
 * A slash command the composer knows how to deliver.
 *
 * Deliberately NOT the agent's whole command list, which is one click away on
 * the Controls tab: the composer carries only commands that change the session
 * the reader is typing into — `compact`, plus the Grove commands this host's
 * config declares (`/grove:<name>`), which the engine runs itself.
 */
export interface ComposerCommand {
  /** The control name `POST /controls/invoke` takes, without the slash. */
  readonly id: string;
  readonly description: string;
}

/** The built-in `/` commands, listed before configured Grove commands. */
export const COMPOSER_COMMANDS: readonly ComposerCommand[] = [
  { id: "compact", description: "Summarize the conversation to free context" },
];

/** Config-declared Grove commands, in the control route's display order. */
export function grovePaletteCommands(controls: readonly SessionControlView[]): readonly ComposerCommand[] {
  return controls
    .filter((control) => control.scope === "grove")
    .map((control) => ({ id: control.name, description: control.detail ?? "Grove command" }));
}

/** One `@` mention: a live peer agent, addressed exactly as the mailbox names it. */
export interface ComposerMention {
  /** `workspace_id` or `workspace_id/agent` — what a mailbox send takes. */
  readonly id: string;
  readonly type: "agent";
  readonly label: string;
  readonly description: string;
}

/**
 * The peers `@` can name, from `GET /mailboxes/contacts`.
 *
 * Only LIVE contacts, because `live` is the directory's whole admission rule —
 * a mention of an agent that cannot be written to would put an address in the
 * brief that the agent's first send would bounce. The writer's own workspace is
 * excluded: mentioning yourself names nobody new.
 */
export function mentionsFromContacts(
  contacts: readonly MailboxContact[] | undefined,
  selfWorkspaceId: string,
): readonly ComposerMention[] {
  return (contacts ?? [])
    .filter((contact) => contact.live && contact.address.workspace_id !== selfWorkspaceId)
    .map((contact) => {
      const { workspace_id: workspace, agent } = contact.address;
      return {
        id: agent ? `${workspace}/${agent}` : workspace,
        type: "agent",
        label: contact.display_name,
        description: `${contact.provider} · ${contact.runtime}`,
      };
    });
}
