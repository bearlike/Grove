import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StatusFooter } from "@/components/grove/shell/status-footer";
import { TooltipProvider } from "@/components/ui/tooltip";
import { project, snapshot, workspace } from "@/tests/fixtures/fleet";
import { EMPTY_COUNTS, EMPTY_PROGRESS, workspaceContext } from "@/lib/grove/adapters/footer";

describe("workspaceContext root context", () => {
  it("takes the root agent identity and context only from the engine-primary session", () => {
    const row = workspace({ id: "root-context", agentName: "claude" });
    row.sessions[0]!.activity.context = { size: 200_000, used: 60_000, used_fraction: 0.3 };
    row.sessions.push({
      ...row.sessions[0]!,
      session: { ...row.sessions[0]!.session, adapter_kind: "child" },
      activity: {
        ...row.sessions[0]!.activity,
        context: { size: 1_000_000, used: 900_000, used_fraction: 0.9 },
      },
    });

    expect(workspaceContext(snapshot([project("Grove", "/repos/grove", [row])]), "root-context"))
      .toMatchObject({
        rootAgent: "claude",
        contextWindow: { size: 200_000, used: 60_000, used_fraction: 0.3 },
      });
  });

  it("uses the primary adapter kind only when the workspace has no agent name", () => {
    const row = workspace({ id: "adapter-fallback", agentName: "" });
    row.sessions[0]!.session.adapter_kind = "codex";

    expect(workspaceContext(snapshot([project("Grove", "/repos/grove", [row])]), "adapter-fallback"))
      .toMatchObject({ rootAgent: "codex" });
  });

  it("uses the latest primary reading after compaction, not a prior child value", () => {
    const row = workspace({ id: "compacted" });
    row.sessions[0]!.activity.context = { size: 200_000, used: 160_000, used_fraction: 0.8 };
    row.sessions.push({
      ...row.sessions[0]!,
      activity: {
        ...row.sessions[0]!.activity,
        context: { size: 200_000, used: 180_000, used_fraction: 0.9 },
      },
    });
    row.sessions[0]!.activity.context = { size: 200_000, used: 40_000, used_fraction: 0.2 };

    expect(workspaceContext(snapshot([project("Grove", "/repos/grove", [row])]), "compacted"))
      .toMatchObject({ contextWindow: { size: 200_000, used: 40_000, used_fraction: 0.2 } });
  });

  it("keeps missing primary context absent rather than inventing a zero", () => {
    const row = workspace({ id: "missing-context" });
    row.sessions[0]!.activity.context = null;

    expect(workspaceContext(snapshot([project("Grove", "/repos/grove", [row])]), "missing-context"))
      .toMatchObject({ rootAgent: "claude", contextWindow: null });
  });

  it("renders the root identity with shared formatted counts and percentage", () => {
    const row = workspace({ id: "rendered-context", agentName: "Claude Code" });
    row.sessions[0]!.activity.context = { size: 200_000, used: 128_450, used_fraction: 0.64225 };
    const context = workspaceContext(snapshot([project("Grove", "/repos/grove", [row])]), "rendered-context")!;
    const html = renderToStaticMarkup(
      <TooltipProvider>
        <StatusFooter
          context={context}
          counts={EMPTY_COUNTS}
          accounts={[]}
          system={null}
          connected
          progress={EMPTY_PROGRESS}
          attention={0}
        />
      </TooltipProvider>,
    );

    // WHO is answering and HOW FULL it is are two groups with a seam between
    // them, not one span — so the identity is asserted on its own element. They
    // used to be nested, which is why the rendered band read as a name running
    // straight into a number with no boundary.
    const agent = html.slice(
      html.indexOf('data-testid="footer-root-agent"'),
      html.indexOf('data-testid="footer-context-window"'),
    );
    expect(agent).toContain("Claude Code");
    // The seam is BETWEEN them: it has to fall in the span separating the two
    // testids, which a document-wide search for the class could not establish.
    expect(agent).toContain('data-testid="footer-seam"');

    const desktop = html.slice(html.indexOf('data-testid="footer-context-window"'));
    expect(desktop).toContain("128.45K / 200K");
    // A WHOLE percent in the band. The counts keep their two decimals — they
    // are the compact figure §3 grants the precision exception to — but the
    // percentage is a glance reading, and `64.23%` is three characters of a
    // digit nobody compares to a hundredth in permanently visible chrome. The
    // Activity card still renders `percent` with its decimals; this is
    // `percentWhole`, rounded from the same `percentValue` the tone ramp reads,
    // so the two can never disagree about the number itself.
    expect(desktop).toContain("64%");
    expect(desktop).not.toContain("64.23%");
  });

  it("drops the raw COUNTS below `xl`, never the percentage (#815)", () => {
    // This group is two figures and cannot shrink, so below `xl` it pushed the
    // workspace section through its own closing rule and into the git
    // section's first value — measured at 1024px on the built page, the
    // section ended at 429px with this drawn to 449px.
    //
    // Clipping the section was the first repair and it was WORSE: the box cuts
    // the right-hand child, which is the percentage, so the one value carrying
    // urgency and the tone ramp was deleted while the raw counts survived
    // whole. A narrow band shows fewer values rather than partial ones, so the
    // counts are gated and the percentage never is.
    const row = workspace({ id: "narrow-context", agentName: "Claude Code" });
    row.sessions[0]!.activity.context = { size: 200_000, used: 128_450, used_fraction: 0.64225 };
    const context = workspaceContext(snapshot([project("Grove", "/repos/grove", [row])]), "narrow-context")!;
    const html = renderToStaticMarkup(
      <TooltipProvider>
        <StatusFooter
          context={context}
          counts={EMPTY_COUNTS}
          accounts={[]}
          system={null}
          connected
          progress={EMPTY_PROGRESS}
          attention={0}
        />
      </TooltipProvider>,
    );
    // From the group's OPENING `<span`, not from its testid: the exact counts
    // ride the `title` attribute, which precedes the testid on that tag.
    const group = html.slice(html.lastIndexOf("<span", html.indexOf('data-testid="footer-context-window"')));

    const counts = /<span class="([^"]*)"[^>]*>128\.45K/.exec(group)?.[1] ?? "";
    expect(counts).toMatch(/hidden[^"]*xl:inline/);

    const percent = /<span class="([^"]*)"[^>]*>\s*64%/.exec(group)?.[1] ?? "";
    expect(percent).not.toMatch(/\b(sm|md|lg|xl|2xl):/);
    expect(percent).toContain("tabular-nums");

    // The counts stay reachable, exactly and unrounded, on the group's title.
    expect(group).toContain("128,450 / 200,000");

    // THE GROUP'S WRAPPER MIRRORS ITS OWN RIGIDITY. This group is two figures
    // and cannot elide, so a `min-w-0` wrapper shrinks past it, the child
    // overflows, and `Section`'s clip cuts it — measured at 1024px on the
    // built page, the reading sat 4.9px past its wrapper with the `%` sliced
    // mid-glyph, which reads as uneven spacing around the divider rather than
    // as a clipped value. `Groups` reads the group's own `shrink-0`.
    const wrappers = [...html.matchAll(/<span class="(inline-flex items-center gap-1 px-1[^"]*)"/g)];
    const at = html.indexOf('data-testid="footer-context-window"');
    const own = wrappers.filter((m) => m.index < at).pop()?.[1];
    expect(own, "the context group must render through Groups").toBeDefined();
    expect(own).toContain("shrink-0");
    expect(own!.split(/\s+/)).not.toContain("min-w-0");
  });
});
