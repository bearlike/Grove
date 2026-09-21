import { describe, expect, it } from "vitest";

import type {
  BillingAccountView,
  DashboardSnapshotView,
  SubscriptionWindowView,
  UsageQuotasView,
  WhoamiView,
} from "@/lib/grove/api";
import {
  EMPTY_CONTEXT,
  EMPTY_COUNTS,
  EMPTY_PROGRESS,
  MAX_INLINE_ACCOUNTS,
  accountSummaries,
  accountSummary,
  accountTierLabel,
  fitAccounts,
  fleetCounts,
  fleetFigures,
  hiddenNeedsAttention,
  projectContext,
  projectSubpath,
  percentLabel,
  quotaSummary,
  quotaTone,
  quotaUrgent,
  sessionSummary,
  systemFacts,
  tightestWindow,
  workspaceContext,
} from "@/lib/grove/adapters/footer";
import { project, snapshot, workspace } from "@/tests/fixtures/fleet";

function window(
  label: string,
  partial: Partial<SubscriptionWindowView> = {},
): SubscriptionWindowView {
  return {
    label,
    scope: "session",
    evidence: "provider_endpoint",
    ...partial,
  };
}

function account(
  id: string,
  partial: Partial<BillingAccountView> = {},
): BillingAccountView {
  return {
    account_id: id,
    label: id,
    provider: "claude_code",
    billing_mode: "subscription",
    status: "ok",
    windows: [window("5h", { used_percent: 10 })],
    ...partial,
  };
}

function quotas(accounts: readonly BillingAccountView[]): UsageQuotasView {
  return {
    accounts: [...accounts],
    coverage: {
      sources: [],
      degraded_source_count: 0,
      cost_available: false,
      quota_available: false,
    },
  };
}

function summary(id: string, partial: Partial<ReturnType<typeof accountSummary>> = {}) {
  return { ...accountSummary(account(id)), ...partial };
}

describe("fleetCounts", () => {
  it("counts only a literally blocked primary session", () => {
    const rows = [
      workspace({ id: "waiting", state: "waiting", needsAttention: true }),
      workspace({ id: "error", state: "error", needsAttention: true }),
      workspace({ id: "blocked", state: "blocked", needsAttention: true }),
    ];

    expect(fleetCounts(snapshot([project("Grove", "/repos/grove", rows)])).blocked).toBe(1);
  });

  it("counts idle literally and leaves every unclassified state out", () => {
    const unclassified = ["starting", "waiting", "error", "unknown"] as const;

    for (const state of unclassified) {
      expect(
        fleetCounts(snapshot([project("Grove", "/repos/grove", [workspace({ id: state, state })])])),
        state,
      ).toEqual(EMPTY_COUNTS);
    }
    expect(
      fleetCounts(snapshot([project("Grove", "/repos/grove", [workspace({ id: "idle", state: "idle" })])])),
    ).toEqual({ working: 0, idle: 1, blocked: 0 });
  });

  it("leaves a workspace with no sessions out of every count", () => {
    expect(
      fleetCounts(snapshot([project("Grove", "/repos/grove", [workspace({ id: "empty", hasSession: false })])])),
    ).toEqual(EMPTY_COUNTS);
  });

  it("counts only the primary session once per workspace", () => {
    const row = workspace({ id: "primary", state: "idle" });
    row.sessions.push({ ...row.sessions[0], activity: { ...row.sessions[0].activity, state: "working" } });

    expect(fleetCounts(snapshot([project("Grove", "/repos/grove", [row])]))).toEqual({
      working: 0,
      idle: 1,
      blocked: 0,
    });
  });

  it("counts workspaces across every project", () => {
    expect(
      fleetCounts(
        snapshot([
          project("One", "/repos/one", [workspace({ id: "one", state: "working" })]),
          project("Two", "/repos/two", [workspace({ id: "two", state: "blocked" })]),
        ]),
      ),
    ).toEqual({ working: 1, idle: 0, blocked: 1 });
  });

  it("is empty without a snapshot", () => {
    expect(fleetCounts(undefined)).toEqual(EMPTY_COUNTS);
  });
});

describe("workspace and project context", () => {
  it("uses the live activity branch instead of the create-time branch", () => {
    const row = workspace({ id: "branch", branch: "feat/created" });
    row.branch = "fix/live";

    expect(workspaceContext(snapshot([project("Grove", "/repos/grove", [row])]), "branch")?.branch).toBe(
      "fix/live",
    );
  });

  it("falls back to the create-time branch for an older daemon", () => {
    const row = workspace({ id: "legacy", branch: "feat/created" });
    row.branch = "";

    expect(workspaceContext(snapshot([project("Grove", "/repos/grove", [row])]), "legacy")?.branch).toBe(
      "feat/created",
    );
  });

  it("returns null for an unknown workspace", () => {
    expect(workspaceContext(snapshot([]), "gone")).toBeNull();
  });

  it("names a cut worktree but not a root-placed workspace", () => {
    const cut = workspace({ id: "cut" });
    const root = workspace({ id: "root" });
    root.state.worktree_path = root.state.repo_root;
    const data = snapshot([project("Grove", "/repos/grove", [cut, root])]);

    expect(workspaceContext(data, "cut")?.worktree).toBe("cut");
    expect(workspaceContext(data, "root")?.worktree).toBeNull();
  });

  it("returns only a cwd safely below the repo root", () => {
    expect(projectSubpath("/repos/grove", "/repos/grove")).toBeNull();
    expect(projectSubpath("/repos/grove", "/repos/grove/packages/webapp")).toBe("packages/webapp");
    expect(projectSubpath("/repos/grove", "/repos/groveland")).toBeNull();
  });

  it("does not turn a root placement into a root-name subpath", () => {
    const root = "/repos/grove";
    const cwd = root;

    expect(projectSubpath(root, cwd)).toBeNull();
  });

  it("returns no project context without a selected or matching project", () => {
    const data = snapshot([project("Grove", "/repos/grove", [])]);
    // No selection means no scope, even if a malformed cached payload happened
    // to contain a project whose cwd compares equal to null at runtime.
    const malformed = {
      ...data,
      projects: [{ ...data.projects[0], cwd: null }],
    } as unknown as DashboardSnapshotView;

    expect(projectContext(malformed, null)).toBe(EMPTY_CONTEXT);
    expect(projectContext(data, "/repos/gone")).toBe(EMPTY_CONTEXT);
  });
});

describe("subscription summaries", () => {
  it("picks the individual window nearest its ceiling, never a sum", () => {
    expect(
      tightestWindow([window("5h", { used_percent: 2 }), window("7d", { used_percent: 68 })]),
    ).toMatchObject({ label: "7d", used_percent: 68 });
  });

  it("reads the reported remaining percentage when used percentage is absent", () => {
    const inverse = window("5h", { remaining_percent: 32 });

    expect(tightestWindow([window("7d", { used_percent: 10 }), inverse])).toBe(inverse);
    expect(accountSummary(account("inverse", { windows: [inverse] }))).toMatchObject({
      percent: 68,
      window: "5h",
    });
  });

  it("keeps one unmeasured window so the account can name it", () => {
    const unmeasured = window("7d");

    expect(tightestWindow([unmeasured, window("5h")])).toBe(unmeasured);
  });

  it("excludes spend-reporting API-key accounts", () => {
    expect(accountSummaries(quotas([account("subscription"), account("spend", { billing_mode: "api_key" })]))).toHaveLength(1);
  });

  it("ranks the FULLEST account first — the strip shows two of N", () => {
    // REVERSED deliberately from "preserve the daemon's order". That rule
    // bought stability and cost correctness: an exhausted account could sit
    // behind `+2 accounts` while two idle ones held the band. Percentages
    // move over minutes rather than frames, and each row now leads with its
    // provider's mark, so a reader re-finds an account by its logo.
    const summaries = accountSummaries(
      quotas([
        account("low", { windows: [window("5h", { used_percent: 1 })] }),
        account("high", { windows: [window("5h", { used_percent: 99 })] }),
      ]),
    );

    expect(summaries.map((item) => item.accountId)).toEqual(["high", "low"]);
  });

  it("ranks on the TIGHTEST window, so 5h never outranks 7d or the reverse", () => {
    // The question the user raised: either span can be the one closest to
    // stopping you, so neither may be preferred. `tightestWindow` already
    // answers it per account; ranking is one sort on top of that.
    const summaries = accountSummaries(
      quotas([
        account("weekly-tight", { windows: [window("5h", { used_percent: 3 }), window("7d", { used_percent: 91 })] }),
        account("session-tight", { windows: [window("5h", { used_percent: 96 }), window("7d", { used_percent: 12 })] }),
      ]),
    );

    expect(summaries.map((item) => item.accountId)).toEqual(["session-tight", "weekly-tight"]);
    expect(summaries.map((item) => item.window)).toEqual(["5h", "7d"]);
  });

  it.each([
    ["unknown first in the payload", ["unknown", "quiet", "busy"]],
    ["unknown last in the payload", ["quiet", "busy", "unknown"]],
  ])("sorts an UNMEASURED account last — unknown is not low (%s)", (_name, order) => {
    // BOTH ARRANGEMENTS, and three accounts rather than two. MUTATION-TESTED:
    // with a single pair the comparator only ever received the measured
    // account as its first argument, so corrupting the `?? -1` on that side
    // changed nothing and the guard passed while the rule was broken.
    const byId = {
      unknown: account("unknown", { windows: [window("5h", { used_percent: null })] }),
      quiet: account("quiet", { windows: [window("5h", { used_percent: 4 })] }),
      busy: account("busy", { windows: [window("5h", { used_percent: 77 })] }),
    };
    const summaries = accountSummaries(quotas(order.map((id) => byId[id as keyof typeof byId])));

    expect(summaries.map((item) => item.accountId)).toEqual(["busy", "quiet", "unknown"]);
  });

  it("keeps the daemon's order for a TIE, so equals never trade places", () => {
    const summaries = accountSummaries(
      quotas([
        account("first", { windows: [window("5h", { used_percent: 50 })] }),
        account("second", { windows: [window("5h", { used_percent: 50 })] }),
      ]),
    );

    expect(summaries.map((item) => item.accountId)).toEqual(["first", "second"]);
  });
});

describe("the quota ramp is one table with one urgency rule", () => {
  it.each([
    [0, "success"], [49.9, "success"], [50, "warning"], [79.9, "warning"],
    [80, "destructive"], [140, "destructive"],
  ])("tones %s%% as %s", (percent, tone) => {
    expect(quotaTone(percent)).toBe(tone);
  });

  it("answers null for an unmeasured reading rather than inventing a tier", () => {
    expect(quotaTone(null)).toBeNull();
    expect(quotaUrgent(null)).toBe(false);
  });

  it("marks amber and above as urgent, and nothing below", () => {
    // Urgency is a different question from tone: green is information, amber
    // is a thing to act on before it becomes red. Only urgent readings pulse.
    expect(quotaUrgent(49.9)).toBe(false);
    expect(quotaUrgent(50)).toBe(true);
    expect(quotaUrgent(80)).toBe(true);
  });

  it("prints a WHOLE percent, rounded rather than truncated", () => {
    // A generic provider published `23.63`. The tenth is below the resolution
    // of the decision the reading informs — how much headroom is left — while
    // costing two characters in the band whose scarcest resource is width.
    expect(percentLabel(23.63)).toBe("24");
    expect(percentLabel(7)).toBe("7");
    expect(percentLabel(99.6)).toBe("100");
  });

  it("keeps the rounding OUT of every threshold", () => {
    // THE ONE WAY THIS COULD LIE: 99.6% printing `100%` must not also become
    // exhausted. Display rounds up to the limit; the ramp still reads the raw
    // number and reports `destructive` because 99.6 >= 80, never because the
    // label said 100. Mutation-tested by rounding before the comparison, which
    // turns the 99.6 case into a claim the provider never made.
    expect(percentLabel(99.6)).toBe("100");
    expect(quotaSummary([summary("nearly", { percent: 99.6 })])).toEqual({ state: "near", count: 1 });
    expect(quotaSummary([summary("really-at-limit", { percent: 100 })])).toEqual({ state: "exhausted", count: 1 });
  });
});

describe("account fitting", () => {
  const six = Array.from({ length: 6 }, (_, index) => summary(`account-${index}`));

  it("never shows more than the two-account hard ceiling", () => {
    expect(fitAccounts(six, 99).inline).toHaveLength(MAX_INLINE_ACCOUNTS);
  });

  it("uses a zero or one-account viewport capacity without losing accounts", () => {
    const one = fitAccounts(six, 1);
    const zero = fitAccounts(six, 0);

    expect(one.inline).toHaveLength(1);
    expect(one.hidden).toHaveLength(5);
    expect(zero.inline).toHaveLength(0);
    expect(zero.hidden).toHaveLength(6);
  });

  it("keeps every account in the complete popover list", () => {
    const fit = fitAccounts(six, 1);

    expect(fit.all).toEqual(six);
    expect(fit.all).toHaveLength(fit.inline.length + fit.hidden.length);
  });

  it("shows the final account directly instead of hiding it behind a one-item overflow", () => {
    expect(fitAccounts([summary("one"), summary("two")], 1).inline).toHaveLength(2);
    expect(fitAccounts([summary("one"), summary("two"), summary("three")], 1)).toMatchObject({
      inline: [expect.objectContaining({ accountId: "one" })],
      hidden: [expect.objectContaining({ accountId: "two" }), expect.objectContaining({ accountId: "three" })],
    });
  });

  it("signals exhausted or unhealthy hidden accounts, but not healthy ones", () => {
    expect(hiddenNeedsAttention([summary("exhausted", { percent: 100 })])).toBe(true);
    expect(hiddenNeedsAttention([summary("stale", { status: "stale" })])).toBe(true);
    expect(hiddenNeedsAttention([summary("healthy", { percent: 99, status: "ok" })])).toBe(false);
  });
});

describe("narrow-band summaries", () => {
  it("puts blocked above larger working and idle counts", () => {
    expect(sessionSummary({ blocked: 1, working: 9, idle: 12 })).toEqual({
      state: "blocked",
      count: 1,
    });
  });

  it("puts working above idle", () => {
    expect(sessionSummary({ blocked: 0, working: 1, idle: 9 })).toEqual({
      state: "working",
      count: 1,
    });
  });

  it("is empty when every session count is zero", () => {
    expect(sessionSummary(EMPTY_COUNTS)).toEqual({ state: "empty", count: 0 });
  });

  it("puts a final exhausted account above preceding healthy accounts", () => {
    expect(
      quotaSummary([summary("healthy-one", { percent: 10 }), summary("healthy-two", { percent: 20 }), summary("last", { percent: 100 })]),
    ).toEqual({ state: "exhausted", count: 1 });
  });

  it("puts near-limit accounts above stale and ordinary account counts", () => {
    expect(
      quotaSummary([
        summary("stale", { stale: true }),
        summary("ordinary", { percent: 10 }),
        summary("near", { percent: 99 }),
      ]),
    ).toEqual({ state: "near", count: 1 });
  });

  it("includes the 80 and 100 percent severity boundaries", () => {
    expect(quotaSummary([summary("at-near", { percent: 80 })])).toEqual({ state: "near", count: 1 });
    expect(quotaSummary([summary("at-exhausted", { percent: 100 })])).toEqual({
      state: "exhausted",
      count: 1,
    });
  });

  it("puts stale accounts above a plain account count", () => {
    expect(
      quotaSummary([summary("ordinary", { percent: 10 }), summary("stale", { stale: true })]),
    ).toEqual({ state: "stale", count: 1 });
  });

  it("falls back to the full plain account count", () => {
    expect(quotaSummary([summary("one", { percent: 10 }), summary("two", { percent: 20 })])).toEqual({
      state: "count",
      count: 2,
    });
  });

  it("counts every account in the winning severity tier", () => {
    expect(
      quotaSummary([
        summary("first-exhausted", { percent: 100 }),
        summary("healthy", { percent: 10 }),
        summary("second-exhausted", { percent: 120 }),
      ]),
    ).toEqual({ state: "exhausted", count: 2 });
  });

  it("is empty without accounts", () => {
    expect(quotaSummary([])).toEqual({ state: "empty", count: 0 });
  });
});

describe("system facts", () => {
  it("returns null without daemon identity", () => {
    expect(systemFacts(undefined)).toBeNull();
  });

  it("does not claim an unnamed update is available", () => {
    const identity = {
      version: "1.0.0",
      started_at: "2026-09-18T00:00:00Z",
      uptime_seconds: 1,
      host: "host",
      user: "user",
      platform: "linux",
      python_version: "3.13",
      update_available: true,
      latest_version: null,
    } satisfies WhoamiView;

    expect(systemFacts(identity)).toMatchObject({ updateAvailable: false, latestVersion: null });
  });
});

describe("fleetFigures says how the fleet is doing, once", () => {
  const counts = { working: 2, idle: 1, blocked: 1 };
  const progress = { fraction: 0.6, reported: 8, tickets: 11 };

  it("drops zero figures rather than greying them", () => {
    const keys = fleetFigures({ working: 2, idle: 0, blocked: 0 }, EMPTY_PROGRESS, 0).map((f) => f.key);
    expect(keys).toEqual(["working"]);
  });

  it("shows attention only when it exceeds blocked — the two overlap", () => {
    // Attention folds blocked in, so `1 blocked` beside `1 need you` is one
    // workspace said twice.
    expect(fleetFigures(counts, EMPTY_PROGRESS, 1).map((f) => f.key)).not.toContain("attention");
    expect(fleetFigures(counts, EMPTY_PROGRESS, 3).map((f) => f.key)).toContain("attention");
  });

  it("folds the ticket count and its mean progress into ONE figure with the denominator on hover", () => {
    const tickets = fleetFigures(counts, progress, 0).find((f) => f.key === "tickets")!;
    expect(tickets.count).toBe(11);
    expect(tickets.percent).toBe(60);
    expect(tickets.title).toContain("8 of 11");
  });

  it("carries no percentage when nothing has reported, and says so", () => {
    const tickets = fleetFigures(counts, { fraction: null, reported: 0, tickets: 4 }, 0).find((f) => f.key === "tickets")!;
    expect(tickets.percent).toBeNull();
    expect(tickets.title).toContain("none has reported");
  });

  it("gives every figure exactly one word, so no count rides on hue alone", () => {
    for (const figure of fleetFigures(counts, progress, 3)) expect(figure.word.length).toBeGreaterThan(0);
  });
});

describe("accountTierLabel drops the provider the mark already says", () => {
  const base = { accountId: "a", label: "x@example.com", provider: "claude_code" as const, percent: 1, window: "7d", status: "ok" as const, stale: false };

  it("strips a leading provider word", () => {
    expect(accountTierLabel({ ...base, shortLabel: "Claude max 20x" })).toBe("max 20x");
  });

  it("leaves a label that does not start with the provider alone", () => {
    // A provider with no plan falls back to its full label, which may be an
    // email — that row has no other name and must not lose its first word.
    expect(accountTierLabel({ ...base, shortLabel: "x@example.com" })).toBe("x@example.com");
  });

  it("only strips the WORD, never a prefix of a longer word", () => {
    expect(accountTierLabel({ ...base, shortLabel: "Claudette plan" })).toBe("Claudette plan");
  });
});
