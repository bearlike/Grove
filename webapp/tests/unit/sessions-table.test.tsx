import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SessionTable } from "@/components/grove/sessions/session-table";
import type { SessionSummaryView } from "@/lib/grove/api";

type Row = SessionSummaryView & { turn_count?: number | null };

/** Shaped like the live daemon's rows: no parsed title, no prompts, a branch
 * and an id on every one. See `sessions-browser.test.ts` for why that matters. */
function session(over: Partial<Row> = {}): Row {
  return {
    session_id: "b3b5108d-1cbf-4ccf-91f9-e1e7d515542c",
    adapter_kind: "claude_code",
    provenance: "fs_discovered",
    primary: false,
    workspace_id: null,
    workspace_title: null,
    workspace_branch: null,
    git_branch: "feat/usage-audit",
    created_at: "2026-08-10T06:30:39.680000Z",
    modified_at: "2026-08-10T22:11:29.394068Z",
    size_bytes: null,
    title: null,
    first_prompt: null,
    last_prompt: null,
    activity: null,
    cwd: "/repos/grove",
    project: {
      repo_root: "/repos/grove",
      repo_name: "Grove",
      is_worktree: false,
      is_grove_managed: true,
    },
    live: false,
    ...over,
  };
}

const render = (rows: readonly Row[]): string =>
  renderToStaticMarkup(<SessionTable sessions={rows} />);

describe("the Session column is IDENTITY, not a guessed label", () => {
  // THE BUG. The deployed label rule was
  // `workspace_title || git_branch || session_id`, and a host-scoped catalog
  // row carries no parsed title — so on a real host it dropped through to
  // `git_branch` on effectively every row. The Session column and the Branch
  // column printed the same string, and neither said which transcript you were
  // about to open.
  const markup = render([session()]);

  it("shows the session id", () => {
    expect(markup).toContain("b3b5108d");
  });

  it("links to the transcript by the triple the drill-in route resolves by", () => {
    expect(markup).toContain("/sessions/b3b5108d-1cbf-4ccf-91f9-e1e7d515542c");
    expect(markup).toContain("kind=claude_code");
    expect(markup).toContain("cwd=%2Frepos%2Fgrove");
  });

  it("keeps the whole id reachable behind the shortened one", () => {
    expect(markup).toContain("b3b5108d-1cbf-4ccf-91f9-e1e7d515542c");
  });

  it("keeps the branch OUT of the session cell — the reported bug, exactly", () => {
    // Scoped to the first cell rather than counted across the row: the branch
    // legitimately appears twice in the branch column, once as text and once in
    // the entity label's tooltip.
    const firstCell = markup.slice(markup.indexOf("<td"), markup.indexOf("</td>"));
    expect(firstCell).toContain("b3b5108d");
    expect(firstCell).not.toContain("feat/usage-audit");
  });

  it("shows a workspace title as secondary context, never as the identity", () => {
    const titled = render([session({ workspace_title: "Frontend UI migration" })]);
    expect(titled).toContain("Frontend UI migration");
    // The id is still what the link says.
    expect(titled).toContain(">b3b5108d</a>");
  });

  it("lists a row with no recorded cwd but does not link it", () => {
    // The drill-in resolves by `(kind, cwd, session_id)`, so a link without a
    // cwd 404s by construction. Hiding the row would misreport the host.
    const loose = render([session({ cwd: null, project: null })]);
    expect(loose).toContain("b3b5108d");
    expect(loose).not.toContain("<a");
  });
});

describe("wall clock and compute are two figures, never one", () => {
  it("renders both as unmeasured when the backend has not timed the session yet", () => {
    // The caveat this column ships under: the reducer that fills `duration` is
    // still landing, so a live host reads this state on every row today. It
    // must look deliberate, not broken.
    const markup = render([session()]);
    expect(markup).toContain("Wall clock");
    expect(markup).toContain("Compute");
    expect(markup.match(/not measured/g)?.length).toBeGreaterThanOrEqual(2);
  });

  it("renders the two reducers independently, never merged into one column", () => {
    const markup = render([
      session({
        duration: { active_ms: 12_030_000, execution_ms: 27_330_000, confidence: "measured" },
      }),
    ]);
    expect(markup).toContain("3h 20m");
    expect(markup).toContain("7h 35m");
  });
});

describe("a turn count that was never taken", () => {
  it("renders an em dash, never a zero", () => {
    const markup = render([session()]);
    expect(markup).toContain("—");
    expect(markup).not.toMatch(/>0</);
  });

  it("says what the dash means rather than standing mute", () => {
    expect(render([session()])).toContain("Not counted at this scope");
  });

  it("renders a real count, grouped, when there is one", () => {
    expect(render([session({ turn_count: 1234 })])).toContain("1,234");
  });

  it("renders a genuine zero as a zero — it is a measurement, not an absence", () => {
    const markup = render([session({ turn_count: 0 })]);
    expect(markup).toContain(">0<");
    expect(markup).not.toContain("Not counted at this scope");
  });
});

describe("every named thing wears the shared entity vocabulary", () => {
  const markup = render([session()]);

  it("types the project and the branch with the app-wide labels", () => {
    expect(markup).toContain('data-testid="entity-project"');
    expect(markup).toContain('data-testid="entity-branch"');
  });

  it("speaks the kind for a screen reader instead of drawing it only", () => {
    expect(markup).toContain("Project: ");
    expect(markup).toContain("Branch: ");
  });

  it("marks a session with no repo as a LOCATION, not as a project", () => {
    // A `cwd` outside any repo is a directory; drawing a repo mark over it
    // would assert something the scan never established.
    const loose = render([session({ project: null, cwd: "/tmp/scratch" })]);
    expect(loose).toContain('data-testid="entity-location"');
    expect(loose).not.toContain('data-testid="entity-project"');
  });

  it("leads the agent column with the fleet's brand mark", () => {
    expect(markup).toContain('data-testid="agent-mark"');
    expect(markup).toContain('data-brand="claude"');
  });

  it("brands a codex row as Codex, from the adapter kind alone", () => {
    expect(render([session({ adapter_kind: "codex" })])).toContain('data-brand="codex"');
  });

  it("dates the row with the shared relative-time element", () => {
    expect(markup).toContain('data-testid="relative-time"');
    // The machine-readable instant rides the element regardless of which of the
    // two forms is painted, and the SERVER paints the absolute one — rendering
    // "2h ago" here is what would mismatch on hydration.
    expect(markup).toMatch(/<time [^>]*="2026-08-10T22:11:29\.394068Z"/);
    expect(markup).not.toContain("ago");
  });
});

describe("column geometry comes from the shared table vocabulary", () => {
  const markup = render([session()]);

  it("gives the remainder to EXACTLY ONE column", () => {
    // Two `w-full` columns do not split the remainder — the auto layout hands
    // it to whichever has the wider content. Measured live at 1600px with two:
    // Location 845px and Branch 62px, clipping `feat/usage-audit` to about four
    // characters. With one: Location 576px, Branch 224px.
    // Scoped to `<th>`: the vendored `Table` and its container carry their own
    // `w-full`, which says nothing about column sizing.
    expect(markup.match(/<th [^>]*\bw-full\b/g)?.length).toBe(1);
    expect(markup.match(/<td [^>]*max-w-0 truncate/g)?.length).toBe(1);
  });

  it("caps the second long column instead, so it truncates rather than starves", () => {
    expect(markup).toMatch(/<td [^>]*max-w-56 truncate/);
  });

  it("caps the age column, which paints its ABSOLUTE form before mount", () => {
    // `RelativeTime` is mount-gated, so the server paints
    // `8/10/2026, 3:11:29 PM`. Uncapped, the column is sized by that and then
    // snaps to the width of "2h ago".
    expect(markup).toMatch(/<td [^>]*max-w-28 truncate/);
  });

  it("sizes the count column to its digits and right-aligns them", () => {
    expect(markup).toMatch(/class="[^"]*w-px whitespace-nowrap text-right[^"]*"/);
  });
});

describe("the row is one navigational target, not an eight-character link", () => {
  const markup = render([session()]);

  it("stretches the link over the whole row", () => {
    // `after:absolute after:inset-0` against a `relative` row. Verified live:
    // a hit test 120px from the row's right edge returns this link.
    expect(markup).toContain("after:absolute");
    expect(markup).toContain("after:inset-0");
    expect(markup).toMatch(/<tr [^>]*class="[^"]*\brelative\b/);
  });

  it("highlights the row on keyboard focus, not just on hover", () => {
    // The link's own ring surrounds eight characters; without this a keyboard
    // user gets no sense of which ROW they are on.
    expect(markup).toMatch(/<tr [^>]*focus-within:bg-muted\/50/);
  });

  it("claims a pointer only where there is somewhere to go", () => {
    expect(markup).toContain("after:cursor-pointer");
    expect(render([session({ cwd: null, project: null })])).not.toContain("cursor-pointer");
  });
});

describe("one row height, so the list has a rhythm", () => {
  it("keeps a workspace title on the SAME line as the id", () => {
    // A second line made the two rows that have a title 55px and 49px against
    // 39px for every other row — a stutter twice down the list.
    const titled = render([session({ workspace_title: "Frontend UI migration" })]);
    expect(titled).not.toMatch(/<span class="[^"]*\bblock\b[^"]*"[^>]*>Frontend UI migration/);
    expect(titled).toContain("Frontend UI migration");
  });
});

describe("a location says WHICH worktree, not just which repo", () => {
  it("shows the subdirectory under the repo", () => {
    const markup = render([session({ cwd: "/repos/grove/.worktrees/otel-x" })]);
    expect(markup).toContain("Grove");
    expect(markup).toContain(".worktrees/otel-x");
  });

  it("shows the repo alone for a session that ran at its root", () => {
    expect(render([session()])).not.toContain(".worktrees");
  });

  it("carries the full path as the cell's tooltip", () => {
    expect(render([session({ cwd: "/repos/grove/.worktrees/otel-x" })])).toContain(
      'title="/repos/grove/.worktrees/otel-x"',
    );
  });
});

describe("liveness is shown, because it changes what a row means", () => {
  it("marks a session an agent is running in right now", () => {
    const markup = render([session({ live: true })]);
    expect(markup).toContain('data-live="true"');
    expect(markup).toContain("live");
  });

  it("says nothing at all about a session that is not live", () => {
    expect(render([session()])).toContain('data-live="false"');
  });
});
