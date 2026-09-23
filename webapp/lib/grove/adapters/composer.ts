import type { MailboxContact } from "@/lib/grove/api";

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
 * A closed set on purpose: the agent's own command list is already one click
 * away on the Controls tab, and the composer carries only the commands that
 * change the session the reader is typing into. `compact` is the one today —
 * the engine gives a native session its own `compact` verb rather than typing
 * `/compact` as prose (`WorkspaceManager.invoke_control`).
 */
export interface ComposerCommand {
  /** The control name `POST /controls/invoke` takes, without the slash. */
  readonly id: "compact";
  readonly description: string;
}

/** Every command the `/` menu lists, in display order. */
export const COMPOSER_COMMANDS: readonly ComposerCommand[] = [
  { id: "compact", description: "Summarize the conversation to free context" },
];

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
