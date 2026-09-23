/**
 * Every react-query key in one table.
 *
 * Keys are the invalidation contract between a mutation and the reads it
 * refreshes, so scattering literals is how a mutation quietly stops refreshing
 * something. Each entry is a prefix: `queryClient.invalidateQueries({ queryKey:
 * groveKeys.workspace(id) })` reaches that workspace's peek, commits and
 * sessions because they nest under it.
 */
export const groveKeys = {
  activity: ["grove", "activity"] as const,
  health: ["grove", "health"] as const,
  whoami: ["grove", "whoami"] as const,
  mailboxContacts: ["grove", "mailboxes", "contacts"] as const,

  workspaces: ["grove", "workspaces"] as const,
  workspace: (id: string) => ["grove", "workspaces", id] as const,
  peek: (id: string) => ["grove", "workspaces", id, "peek"] as const,
  workspaceActivity: (id: string) => ["grove", "workspaces", id, "activity"] as const,
  queue: (id: string) => ["grove", "workspaces", id, "queue"] as const,
  fleet: (id: string, sessionId: string) =>
    ["grove", "workspaces", id, "fleet", sessionId] as const,
  todo: (id: string) => ["grove", "workspaces", id, "todo"] as const,
  watches: (id: string) => ["grove", "workspaces", id, "watches"] as const,
  history: (id: string) => ["grove", "workspaces", id, "history"] as const,
  panels: (id: string) => ["grove", "workspaces", id, "panels"] as const,
  diagram: (id: string) => ["grove", "workspaces", id, "diagram"] as const,
  commits: (id: string) => ["grove", "workspaces", id, "commits"] as const,
  diff: (id: string) => ["grove", "workspaces", id, "diff"] as const,
  controls: (id: string) => ["grove", "workspaces", id, "controls"] as const,
  provision: (id: string) => ["grove", "workspaces", id, "provision"] as const,
  pane: (id: string) => ["grove", "workspaces", id, "pane"] as const,
  sessions: (id: string) => ["grove", "workspaces", id, "sessions"] as const,
  sessionCandidates: (id: string) =>
    ["grove", "workspaces", id, "sessions", "candidates"] as const,
  turns: (id: string, sessionId: string) =>
    ["grove", "workspaces", id, "sessions", sessionId, "turns"] as const,
  // Keyed by the CALL, not by the transcript: a body is immutable once the call
  // settles (it is only fetchable because it settled), so one opened disclosure
  // caches independently of the turn window that named it and survives every
  // poll that rewrites `turns`.
  tool: (id: string, sessionId: string, toolUseId: string) =>
    ["grove", "workspaces", id, "sessions", sessionId, "tools", toolUseId] as const,

  catalog: (limit?: number) => ["grove", "sessions", "catalog", limit ?? null] as const,
  gallery: () => ["grove", "gallery"] as const,
  galleryDocument: (id: string) => ["grove", "gallery", id, "document"] as const,
  galleryPreview: (id: string, digest: string) =>
    ["grove", "gallery", id, "preview", digest] as const,
  catalogTurns: (sessionId: string, kind: string, cwd: string) =>
    ["grove", "sessions", sessionId, "turns", kind, cwd] as const,

  // Keyed by REPO, not by workspace: two workspaces on one repo tracking the
  // same issue are one read, and a tracker's answer belongs to the repo's
  // provider config rather than to whoever asked for it.
  tickets: (repo: string) => ["grove", "tickets", repo] as const,
  ticketProviders: (repo: string) => ["grove", "tickets", repo, "providers"] as const,
  assignedTickets: (repo: string) => ["grove", "tickets", repo, "assigned"] as const,
  ticket: (repo: string, provider: string, id: string) =>
    ["grove", "tickets", repo, provider, id] as const,

  agents: (repo: string) => ["grove", "agents", repo] as const,
  // Keyed by agent as well as repo: two agents on one repo legitimately offer
  // different catalogs, and a shared key would serve one agent's models to the
  // other for as long as the query stayed fresh.
  models: (repo: string, agent: string) => ["grove", "models", repo, agent] as const,
  branches: (repo: string, scope: "local" | "remote") =>
    ["grove", "branches", repo, scope] as const,
  defaults: (repo: string) => ["grove", "defaults", repo] as const,
  sharePolicy: (repo: string) => ["grove", "share-policy", repo] as const,

  usage: ["grove", "usage"] as const,
  usageSummary: (filters: Record<string, string>) =>
    ["grove", "usage", "summary", filters] as const,
  usageActivity: (filters: Record<string, string>, metric: string) =>
    ["grove", "usage", "activity", filters, metric] as const,
  usageSessions: (filters: Record<string, string>, sort: string, cursor: string | null) =>
    ["grove", "usage", "sessions", filters, sort, cursor] as const,
  usageBreakdown: (filters: Record<string, string>, dimension: string) =>
    ["grove", "usage", "breakdowns", filters, dimension] as const,
  usageSeries: (filters: Record<string, string>, dimension: string, metric: string) =>
    ["grove", "usage", "series", filters, dimension, metric] as const,
  usageQuotas: ["grove", "usage", "quotas"] as const,
  usageFindings: (filters: Record<string, string>) =>
    ["grove", "usage", "findings", filters] as const,
  usageBashCommands: (filters: Record<string, string>) =>
    ["grove", "usage", "bash-commands", filters] as const,
} as const;
