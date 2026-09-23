import { describe, expect, it } from "vitest";

import { COMPOSER_COMMANDS, mentionsFromContacts } from "@/lib/grove/adapters";
import type { MailboxContact } from "@/lib/grove/api";

const SELF = "a".repeat(32);
const PEER = "b".repeat(32);

function contact(overrides: Partial<MailboxContact> & { workspace: string; agent?: string }): MailboxContact {
  const { workspace, agent = "", ...rest } = overrides;
  return {
    address: { workspace_id: workspace, agent },
    display_name: "Reviewer",
    provider: "claude_code",
    runtime: "host",
    live: true,
    ...rest,
  };
}

describe("the `/` menu", () => {
  it("offers exactly the commands Grove can deliver, named as the control route takes them", () => {
    // A closed set: the agent's full command list lives on the Controls tab.
    expect(COMPOSER_COMMANDS.map((command) => command.id)).toEqual(["compact"]);
    for (const command of COMPOSER_COMMANDS) {
      expect(command.id.startsWith("/"), "the route strips the slash itself").toBe(false);
      expect(command.description.length).toBeGreaterThan(0);
    }
  });
});

describe("the `@` menu", () => {
  it("lists only LIVE peers, because a dead address bounces the agent's first send", () => {
    const mentions = mentionsFromContacts(
      [contact({ workspace: PEER }), contact({ workspace: "c".repeat(32), live: false })],
      SELF,
    );
    expect(mentions.map((mention) => mention.id)).toEqual([PEER]);
  });

  it("never offers the writer's own workspace", () => {
    expect(mentionsFromContacts([contact({ workspace: SELF })], SELF)).toEqual([]);
  });

  it("addresses an agent slot exactly as the mailbox names it", () => {
    const [bare] = mentionsFromContacts([contact({ workspace: PEER })], SELF);
    const [slotted] = mentionsFromContacts([contact({ workspace: PEER, agent: "reviewer" })], SELF);
    expect(bare?.id).toBe(PEER);
    expect(slotted?.id).toBe(`${PEER}/reviewer`);
  });

  it("labels a peer by its display name and describes how it runs", () => {
    const [mention] = mentionsFromContacts([contact({ workspace: PEER, runtime: "container" })], SELF);
    expect(mention).toEqual({
      id: PEER,
      type: "agent",
      label: "Reviewer",
      description: "claude_code · container",
    });
  });

  it("is empty, not an error, before the directory has loaded", () => {
    expect(mentionsFromContacts(undefined, SELF)).toEqual([]);
  });
});
