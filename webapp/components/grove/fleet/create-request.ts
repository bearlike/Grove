export const TICKET_PROVIDERS = ["gitea", "github", "linear"] as const;
export type TicketProvider = (typeof TICKET_PROVIDERS)[number];
