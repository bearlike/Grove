import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { WorkingLoader, WorkingMark } from "@/components/grove/working-loader";

/**
 * The loader's own markup, plus the one thing about it that is a CONTRACT
 * rather than a look: it renders only while the agent is working, and that
 * gate lives in `transcript.tsx`.
 *
 * The gate is checked as a SOURCE census rather than by rendering the footer.
 * `ThreadPane` needs a live assistant-ui runtime, a react-query client and a
 * resolved workspace, so there is no `node`-environment artifact to inspect —
 * the same reason `displayed-defaults.test.ts` and `launch-control-row.test.ts`
 * read source. Mutation-tested both ways: dropping `working &&` from the footer
 * turns the first assertion red, and removing the `working` field from
 * `GroveThreadState` turns the second red.
 */
const TRANSCRIPT = "components/grove/workspace/transcript.tsx";
const RUNTIME = "lib/grove/runtime/thread.ts";

async function source(path: string): Promise<string> {
  const { readFile } = await import("node:fs/promises");
  return readFile(new URL(`../../${path}`, import.meta.url), "utf8");
}

describe("WorkingLoader", () => {
  it("labels itself Working and announces politely", () => {
    const html = renderToStaticMarkup(<WorkingLoader />);

    expect(html).toContain('data-testid="working-loader"');
    expect(html).toContain('data-slot="generation-loader"');
    expect(html).toContain("Working");
    // A courtesy report, never an interruption — the same contract
    // `SendingEcho` states one sibling above it in the footer.
    expect(html).toContain('role="status"');
  });

  it("uses the rounded cell, not the component's circular default", () => {
    const html = renderToStaticMarkup(<WorkingLoader />);

    // `rounded-[3px]` is the `rounded` variant; `rounded-full` is `dots`. The
    // pair is asserted together so passing no variant at all cannot pass: the
    // default would satisfy a bare "has a cell shape" check.
    expect(html).toContain("rounded-[3px]");
    expect(html).not.toContain("rounded-full");
  });

  it("holds one frame on the server, so hydration has nothing to disagree about", () => {
    // Every cell's opacity is a pure function of `tick`, which starts at 0 and
    // only moves in an effect. A server render that animated would produce
    // markup the first client render could not reproduce — the same rule
    // `relative-time.tsx` follows for the clock.
    expect(renderToStaticMarkup(<WorkingLoader />)).toBe(
      renderToStaticMarkup(<WorkingLoader />),
    );
  });

  it("is mounted only while the agent is working", async () => {
    const transcript = await source(TRANSCRIPT);

    expect(transcript).toContain("{working && <WorkingLoader />}");
  });

  it("exempts a vendored variant's value from lint:styling, and nothing else", async () => {
    // `variant="rounded"` names an upstream variant; `className` must stay
    // fully scanned or the exemption becomes the way around the linter. Pinned
    // here rather than in a linter-specific file because this component is the
    // one real caller, and an exemption with no caller is how one grows.
    const lint = await source("scripts/lint-styling.ts");

    expect(lint).toContain("stripVariantValues(stripComments(");
    // The exemption is scoped to exactly one prop name. Widening it to
    // `className` — or to `(variant|className)` — is the regression this
    // asserts against, and it is the only way this rule could hide a real
    // hand-rolled radius.
    const scope = lint.match(/source\.replace\(\/\((\\b[a-z|]+)=/)?.[1];
    expect(scope).toBe("\\bvariant");
  });

  it("reads the agent's working state, which already folds in sub-agents", async () => {
    const runtime = await source(RUNTIME);

    // One derivation, two consumers (the loader and the interrupt). A second
    // read of `activity.state` anywhere would be the drift this pins against.
    expect(runtime).toContain("working: agentIsWorking(snapshot, workspaceId, sessionId)");
    expect(runtime.match(/agentIsWorking\(/g)).toHaveLength(2);
  });

  it("sizes the matrix off the label's own ramp step, at the theme boundary", async () => {
    const css = await source("app/globals.css");

    // Measured before: a 25.5px matrix beside a 12.8px label — twice the height
    // of its own word. Both the cell and the gap are pinned, because a gap left
    // at the old scale re-opens the same disproportion.
    expect(css).toMatch(
      /\[data-slot="generation-loader"\] > div > span \{\s*width: calc\(var\(--text-sm\) \* 0\.375\);\s*height: calc\(var\(--text-sm\) \* 0\.375\);/,
    );
    expect(css).toMatch(/\[data-slot="generation-loader"\] > div \{\s*gap: calc\(var\(--text-sm\) \* 0\.1875\);/);

    // NOT `em`, and this is the trap worth pinning: `text-sm` is on the
    // vendored LABEL, the grid's SIBLING, so an em on the grid resolves against
    // the ROOT and changes nothing (measured 6.39px either way). Setting a
    // font-size on the loader root to "fix" that makes the label's own text-sm
    // compound to 10.4px, under the 12px floor.
    expect(css).not.toMatch(/\[data-slot="generation-loader"\] \{\s*font-size:/);

    // And it must NOT be repaired at the call site: `lint:styling` forbids it,
    // and the cells are vendored children with no prop reaching them.
    const loader = await source("components/grove/working-loader.tsx");
    expect(loader).not.toMatch(/\bsize-\d/);
  });

  it("advances one visible step per interval fire", async () => {
    const loader = await source("components/grove/working-loader.tsx");

    // The vendored loader positions itself at `Math.floor(tick / 3)`, so a
    // clock that fires a bare `tick` re-renders nine spans twice for every
    // change a reader can see. Feeding `step * 3` is what makes STEP_MS mean a
    // step. If upstream ever changes that divisor this assertion is the thing
    // that should be re-derived — hence pinning both halves together.
    const vendored = await source("components/elements/loading-state.tsx");
    expect(vendored).toContain("Math.floor(tick / 3)");
    expect(loader).toContain("notify(step * 3)");
    expect(loader).toMatch(/const STEP_MS = \d+;/);
  });

  it("scopes the clock's listeners to the clock, never to each subscriber", async () => {
    const loader = await source("components/grove/working-loader.tsx");

    // `addEventListener` dedupes by function reference, so N loaders
    // registering the same `sync` is ONE registry entry and the FIRST unmount
    // removed it for every loader still on screen — every matrix then froze on
    // the next tab switch. The subscribe effect must therefore do nothing but
    // join and leave the set; the add/remove pair belongs to `sync`.
    const effect = loader.match(/subscribers\.add\(setTick\);[\s\S]*?\n  \}, \[\]\);/)?.[0] ?? "";
    expect(effect).not.toBe("");
    expect(effect).not.toContain("addEventListener");
    expect(effect).not.toContain("removeEventListener");

    // And the query itself is created once: `matchMedia` returns a NEW object
    // per call (measured in the browser), so a per-subscriber query can never
    // remove its own listener.
    expect(loader.match(/window\.matchMedia\(/g)).toHaveLength(1);

    // The behavioural half — that a survivor keeps animating after a sibling
    // unmounts — needs a real browser and lives in
    // `tests/e2e/working-loader-clock.spec.ts`. A `node` render mounts no
    // effect at all, so an assertion here would pass against the broken code.
  });

  it("reserves the rail header corner wide enough for the options button", async () => {
    const rail = await source("components/grove/fleet/fleet-tree.tsx");

    // The button is 28px at `right-1.5` (6px) = the last 34px; `pr-10` reserves
    // 32 and was always 2px short. Nothing had occupied that gap until the
    // working mark became the last item on the line. Measured on the built app:
    // overlapping at pr-10, 2.4px clearance at pr-11.
    expect(rail).toMatch(/py-1 pr-11\b/);
    expect(rail).not.toMatch(/py-1 pr-10\b/);
  });

  it("drops the word in the rail but keeps the accessible name", () => {
    const html = renderToStaticMarkup(<WorkingMark />);

    expect(html).toContain('data-testid="working-mark"');
    expect(html).toContain('aria-label="Working"');
    // The 28px title band is all workspace name; the word would crowd it. The
    // label ELEMENT still renders (it is vendored) — it is simply empty, which
    // is why the accessible name has to come from the wrapper.
    expect(html).not.toMatch(/>Working</);
  });

  it("marks a working row in the rail, and only a working one", async () => {
    const rail = await source("components/grove/fleet/fleet-tree.tsx");

    // Inside the title band, gated on the row's own agent state. An absent
    // claim takes no space — the rule the row already follows for its
    // attention and phase marks.
    expect(rail).toContain('agentState === "working" ? (');
    expect(rail).toContain("<WorkingMark");
  });
});
