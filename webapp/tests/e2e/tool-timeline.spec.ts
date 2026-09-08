import { expect, test } from "@playwright/test";
import type { DigestEntryView, SessionDetailView } from "@/lib/grove/api";
import { FIXTURE_WORKSPACES } from "./_fixtures";

test.use({ deviceScaleFactor: 2 });

function tool(name: string, input: Record<string, unknown>, id: string): DigestEntryView {
  return {
    role: "tool", text: `${name} digest`, question: null, file_edit: null, todo: null,
    tool: { name, input, tool_use_id: id, status: "ok", duration_ms: 1234, result: `Response for ${id}` },
  };
}

const fullPath = "/work/project/src/components/app.ts";
const fullCommand = "npm test -- --filter=${VERY_LONG_TEMPLATE_VARIABLE} --reporter=verbose";

/** An edit entry, shaped as the wire sends one. */
function edit(path: string, display: string, oldText: string, newText: string, id: string): DigestEntryView {
  return {
    role: "file_edit", text: `Edit ${display}`, question: null, todo: null,
    tool: { name: "Edit", input: { file_path: path }, tool_use_id: id, status: "ok", duration_ms: 900, result: "Applied" },
    file_edit: { path, display_path: display, old_text: oldText, new_text: newText },
  };
}

for (const theme of ["light", "dark"]) {
  test(`${theme} timeline folds file edits into the run with per-file totals`, async ({ page }) => {
    await page.addInitScript(value => {
      localStorage.setItem("theme", value);
      localStorage.setItem("grove.onboarding.seen", "true");
    }, theme);
    await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async route => {
      const payload: SessionDetailView = await (await route.fetch()).json();
      payload.turns = [{ user_text: "Wire the composer draft", started_at: null, entries: [
        tool("Read", { file_path: "/w/src/composer.tsx" }, "read-1"),
        edit("/w/src/composer.tsx", "src/composer.tsx", "a\nb\n", "a\nB1\nB2\nc\n", "edit-1"),
        edit("/w/src/composer.tsx", "src/composer.tsx", "a\nB1\nB2\nc\n", "a\nB1\nB2\nc\nd\n", "edit-2"),
        edit("/w/src/use-draft.ts", "src/use-draft.ts", "", "x\ny\nz\n", "edit-3"),
        tool("Bash", { command: "npm test" }, "bash-2"),
      ] }];
      await route.fulfill({ json: payload });
    });
    await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
    await page.getByRole("tab", { name: "Transcript", exact: true }).click();
    const timeline = page.getByTestId("tool-call-group").first();
    await expect(timeline).toBeVisible();

    // ONE run, not a group / card / group sandwich: an edit is a tool call.
    await expect(page.getByTestId("tool-call-group")).toHaveCount(1);
    await expect(page.getByTestId("file-edit-card")).toHaveCount(0);
    await expect(timeline.getByRole("button").first())
      .toHaveText("5 steps · 1 command · 1 file read · 2 files changed");

    await timeline.getByRole("button").first().click();
    const editStep = timeline.locator('[data-tool-use-id="edit-1"]');
    await expect(editStep).toContainText("Edited");
    await expect(editStep.getByTestId("tool-target-summary")).toHaveText("composer.tsx");
    await expect(editStep.getByTestId("file-edit-counts")).toContainText("+3");

    // The trailing chips sum EVERY edit to a file: two edits to composer.tsx
    // (+3 −1 and +1 −0) report once, as +4 −1.
    const stats = timeline.getByTestId("tool-timeline-stats");
    await expect(stats.locator("> span")).toHaveCount(2);
    await expect(stats).toContainText("composer.tsx");
    await expect(stats).toContainText("+4");
    await expect(stats).toContainText("use-draft.ts");

    // The expanded step owns a framed diff card, not a second disclosure.
    await editStep.getByRole("button").first().click();
    await expect(editStep.getByTestId("file-edit-diff")).toBeVisible();
    await expect(editStep.locator('[data-slot="diff-viewer-split-line"]').first()).toBeVisible();
    await expect(editStep.getByTestId("file-edit-header")).toContainText("src/composer.tsx");
    await expect(editStep.getByTestId("file-edit-header")).toContainText("+3");
    await expect(editStep.getByTestId("file-edit-expanded")).toHaveAttribute("data-slot", "card");
    expect(await editStep.getByTestId("file-edit-expanded").evaluate(element => parseFloat(getComputedStyle(element).marginTop))).toBeGreaterThanOrEqual(4);
    await expect(editStep.getByTestId("file-edit-expanded").getByRole("button")).toHaveCount(0);
    const creation = timeline.locator('[data-tool-use-id="edit-3"]');
    await creation.getByRole("button").click();
    await expect(creation.getByTestId("file-edit-diff")).toHaveAttribute("data-diff-view", "unified");
    await timeline.screenshot({ path: test.info().outputPath(`timeline-edits-${theme}.png`) });
  });
}

for (const theme of ["light", "dark"]) {
  test(`${theme} timeline counts work and expands native action requests and responses`, async ({ page }) => {
    await page.addInitScript(value => {
      localStorage.setItem("theme", value);
      localStorage.setItem("grove.onboarding.seen", "true");
    }, theme);
    await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async route => {
      const payload: SessionDetailView = await (await route.fetch()).json();
      payload.turns = [{ user_text: "Inspect the app and run tests", started_at: null, entries: [
        tool("Read", { file_path: fullPath }, "read-1"),
        tool("Read", { file_path: fullPath }, "read-2"),
        tool("Bash", { command: fullCommand, description: "Run the tests" }, "bash-1"),
        tool("mcp__docs__lookup", { query: "tool timelines" }, "mcp-1"),
      ] }];
      await route.fulfill({ json: payload });
    });
    await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
    await page.getByRole("tab", { name: "Transcript", exact: true }).click();
    const timeline = page.getByTestId("tool-call-group").first();
    const summary = timeline.getByRole("button", { name: "4 steps · 1 command · 1 file read", exact: true });
    await expect(summary).toHaveAttribute("aria-expanded", "false");
    const icons = timeline.getByTestId("tool-timeline-icons");
    await expect(icons).toBeVisible();
    await expect(icons.locator('[data-slot="app-icon"]')).toHaveCount(3);
    const geometry = await icons.evaluate(element => {
      const discs = [...element.querySelectorAll<HTMLElement>(".tool-icon-stack-disc")];
      const rects = discs.map(disc => disc.getBoundingClientRect());
      const glyphs = discs.map(disc => disc.querySelector("svg")!.getBoundingClientRect());
      const shell = getComputedStyle(element, "::before");
      const box = element.getBoundingClientRect();
      return {
        width: box.width, height: box.height,
        iconSize: glyphs[0].width,
        overlaps: rects.slice(1).every((r, i) => r.left < rects[i].right),
        // Glyphs paint above neighboring rims; their own boxes must stay separated.
        artworkClear: rects.slice(1).every((r, i) => glyphs[i + 1].left > glyphs[i].right),
        radius: parseFloat(shell.borderRadius),
        glyphLayer: getComputedStyle(discs[0].querySelector("svg")!).zIndex,
        shellOpacity: shell.opacity,
        tints: discs.map(disc => disc.style.getPropertyValue("--tool-stack-tint")),
      };
    });
    expect(geometry.width / geometry.height).toBeGreaterThan(1.9);
    expect(geometry.iconSize).toBeGreaterThanOrEqual(11);
    expect(geometry.overlaps).toBe(true);
    expect(geometry.artworkClear).toBe(true);
    expect(geometry.glyphLayer).toBe("1");
    expect(geometry.radius).toBeGreaterThanOrEqual(geometry.height / 2);
    expect(new Set(geometry.tints).size).toBe(3);
    await timeline.screenshot({ path: test.info().outputPath(`tool-timeline-collapsed-${theme}.png`) });
    await expect(timeline.getByTestId("tool-call-request")).toHaveCount(0);
    await summary.click();
    await expect(icons).toBeVisible();
    await expect(icons).toHaveAttribute("data-state", "open");
    await expect.poll(() => icons.evaluate(element => getComputedStyle(element, "::before").opacity)).toBe("0.3");
    expect(await icons.evaluate(element => getComputedStyle(element).opacity)).toBe("1");
    expect((await icons.boundingBox())!.width).toBeCloseTo(geometry.width, 1);
    const read = timeline.locator('[data-tool-use-id="read-1"]');
    await expect(read.getByTestId("tool-target-summary")).toHaveText("app.ts");
    await read.getByRole("button").click();
    await expect(read.getByTestId("tool-call-request")).toContainText(fullPath);
    await read.getByRole("button").click();
    const bash = timeline.locator('[data-tool-use-id="bash-1"]');
    await expect(bash.getByRole("button")).toContainText("Ran");
    await expect(bash.getByTestId("tool-target-summary")).toHaveText("npm test …");
    await expect(timeline).not.toContainText("Used tool");
    await bash.getByRole("button").click();
    await expect(bash.getByTestId("tool-call-request")).toContainText("Run the tests");
    await expect(bash.getByTestId("tool-call-request")).toContainText(fullCommand);
    await expect(bash.getByTestId("tool-call-response")).toContainText("Response for bash-1");
    await expect(timeline.locator('[data-tool-use-id="mcp-1"]')).toContainText("docs / lookup");
    await timeline.screenshot({ path: test.info().outputPath(`tool-timeline-${theme}.png`) });
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(summary).toHaveAttribute("aria-expanded", "false");
    await expect(timeline.getByTestId("tool-call-request")).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(summary).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await timeline.screenshot({ path: test.info().outputPath(`tool-timeline-mobile-${theme}.png`) });
  });
}

for (const theme of ["light", "dark"]) {
  for (const count of [1, 2, 8]) {
    test(`${theme} stack keeps ${count} tool kinds legible`, async ({ page }) => {
      await page.addInitScript(value => {
        localStorage.setItem("theme", value);
        localStorage.setItem("grove.onboarding.seen", "true");
      }, theme);
      const names = ["Write", "Edit", "Bash", "Read", "Grep", "Glob", "TaskStop", "Mailbox"];
      await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async route => {
        const payload: SessionDetailView = await (await route.fetch()).json();
        payload.turns = [{ user_text: "Inspect these tools", started_at: null, entries:
          names.slice(0, count).map((name, i) => tool(name, { file_path: "/w/app.ts", command: "npm test" }, `tool-${i}`)),
        }];
        await route.fulfill({ json: payload });
      });
      await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
      await page.getByRole("tab", { name: "Transcript", exact: true }).click();
      const group = page.getByTestId("tool-call-group");
      const stack = group.getByTestId("tool-timeline-icons");
      const shown = Math.min(count, 5);
      await expect(stack.locator('[data-slot="app-icon"]')).toHaveCount(shown);
      await expect(stack.locator(".tool-icon-stack-disc")).toHaveCount(count > 5 ? 6 : count);
      if (count > 5) await expect(stack.locator(".tool-icon-stack-more")).toHaveText("+3");
      const geometry = await stack.evaluate(element => {
        const box = element.getBoundingClientRect();
        const coins = [...element.querySelectorAll(".tool-icon-stack-disc")];
        return {
          aspect: box.width / box.height,
          clear: coins.every((coin, i) => {
            const svg = coin.querySelector("svg");
            if (!svg || i === coins.length - 1) return true;
            const next = coins[i + 1].querySelector("svg");
            return !next || svg.getBoundingClientRect().right < next.getBoundingClientRect().left;
          }),
          shellOpacity: getComputedStyle(element, "::before").opacity,
          fills: coins.map(coin => getComputedStyle(coin).backgroundImage),
        };
      });
      expect(geometry.aspect).toBeGreaterThan(1.7);
      expect(geometry.clear).toBe(true);
      if (count > 1) expect(new Set(geometry.fills).size).toBeGreaterThan(1);
      await stack.screenshot({ path: test.info().outputPath(`stack-${count}-${theme}.png`) });
      await group.getByRole("button").first().click();
      await expect(stack).toHaveAttribute("data-state", "open");
      await expect.poll(() => stack.evaluate(element => getComputedStyle(element, "::before").opacity)).toBe("0.3");
      expect(await stack.evaluate(element => getComputedStyle(element).opacity)).toBe("1");
      await stack.screenshot({ path: test.info().outputPath(`stack-${count}-${theme}-open.png`) });
      await page.setViewportSize({ width: 390, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    });
  }
}
