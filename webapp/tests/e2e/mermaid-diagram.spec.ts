import { expect, test } from "@playwright/test";
import type { SessionDetailView } from "@/lib/grove/api";
import { FIXTURE_WORKSPACES } from "./_fixtures";

/**
 * The shape agents actually draw — nested subgraphs, labelled edges, fan-in —
 * which is where a layout engine shows its quality. The first label carries
 * markup on purpose: agent prose is untrusted, and the diagram is injected as
 * SVG, so a label must render as text and never as an element that runs script.
 */
const DIAGRAM = [
  "flowchart LR",
  '  subgraph tailnet["Tailscale"]',
  '    Start["<img src=x onerror=window.__mermaidInjected=1>"]',
  "  end",
  '  subgraph staging["anytool-staging"]',
  '    subgraph vm["inference VM"]',
  '      Review["Caddy :443"] --> Ship["SGLang"]',
  "    end",
  '    nat["Cloud NAT"]',
  "  end",
  '  Start -- "ACL grant" --> Review',
  '  Ship -- "tcp 80/443" --> nat --> internet["Internet"]',
  '  Ship -. "denied" .-> private["Any private range"]',
].join("\n");

for (const theme of ["light", "dark"]) {
  test(`${theme} a Mermaid fence renders as a diagram that toggles to its copyable source`, async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.addInitScript(value => {
      localStorage.setItem("theme", value);
      localStorage.setItem("grove.onboarding.seen", "true");
    }, theme);
    await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async route => {
      const payload: SessionDetailView = await (await route.fetch()).json();
      payload.turns = [{ user_text: "Draw the flow", started_at: null, entries: [
        { role: "assistant", text: `Here it is:\n\n\`\`\`mermaid\n${DIAGRAM}\n\`\`\`\n\n\`\`\`python\nprint('hello')\n\`\`\``, question: null, file_edit: null, todo: null },
      ] }];
      await route.fulfill({ json: payload });
    });
    await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
    await page.getByRole("tab", { name: "Transcript", exact: true }).click();

    const diagram = page.locator('[data-slot="mermaid-diagram"]');
    await expect(diagram.locator("svg")).toBeVisible();
    await expect(diagram).toContainText("Caddy :443");
    await expect(diagram).toContainText("ACL grant");
    await expect(diagram.locator("img")).toHaveCount(0);
    expect(await page.evaluate(() => "__mermaidInjected" in window)).toBe(false);
    // Every subgraph is a drawn cluster with its title, and the whole SVG is
    // themed from the page's tokens rather than mermaid's own palette: node
    // fills in a dark theme must be dark.
    await expect(diagram.locator("g.cluster")).toHaveCount(3);
    for (const title of ["Tailscale", "anytool-staging", "inference VM"]) {
      await expect(diagram.locator("g.cluster", { hasText: title })).toHaveCount(1);
    }
    const expectThemedFill = async (scope: ReturnType<typeof page.locator>) => {
      const fill = await scope.locator("g.node rect, g.node path").first().evaluate(el => getComputedStyle(el).fill);
      const [r, g, b] = fill.match(/\d+(\.\d+)?/g)!.map(Number);
      const luminance = (r + g + b) / 3;
      if (theme === "dark") expect(luminance).toBeLessThan(96); else expect(luminance).toBeGreaterThan(200);
      // SVG's default fill; a shape showing it means no stylesheet reached it.
      expect(fill).not.toBe("rgb(0, 0, 0)");
      await expect(scope.locator("path.flowchart-link").first()).toHaveCSS("fill", "none");
    };
    await expectThemedFill(diagram);

    // The fullscreen copy renames every id; its stylesheet must follow the rename
    // or every shape falls back to black.
    await diagram.hover();
    await page.getByRole("button", { name: "Expand diagram" }).click();
    const zoom = page.locator('[data-slot="mermaid-zoom-content"]');
    await expect(zoom.locator("svg")).toBeVisible();
    await expectThemedFill(zoom);
    // The overlay fades in; screenshot it only once it is opaque.
    const overlay = page.locator('[data-slot="mermaid-zoom-overlay"]');
    await expect.poll(() => overlay.evaluate(el => getComputedStyle(el).opacity)).toBe("1");
    await overlay.screenshot({ path: test.info().outputPath(`mermaid-zoom-${theme}.png`) });
    await page.keyboard.press("Escape");
    await expect(zoom).toHaveCount(0);

    const headers = page.locator(".aui-code-header-root");
    await expect(headers).toHaveCount(2);
    const header = headers.filter({ hasText: "mermaid" });
    await expect(page.locator(".aui-md-pre")).toHaveCount(1);
    await page.locator(".aui-md").first().screenshot({ path: test.info().outputPath(`mermaid-diagram-${theme}.png`) });

    await header.getByRole("button", { name: "Show code" }).click();
    await expect(diagram).toHaveCount(0);
    await expect(page.locator(".aui-md-pre")).toHaveCount(2);
    await expect(page.locator(".aui-md-pre").first()).toContainText('Start -- "ACL grant" --> Review');
    await page.locator(".aui-md").first().screenshot({ path: test.info().outputPath(`mermaid-code-${theme}.png`) });

    await header.getByRole("button", { name: "Copy" }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(`${DIAGRAM}\n`);

    await header.getByRole("button", { name: "Show diagram" }).click();
    await expect(diagram.locator("svg")).toBeVisible();
  });
}

/** An entity with enough rows to show both zebra stripes under its header. */
const ENTITY_DIAGRAM = [
  "erDiagram",
  '  scans ||--o{ findings : "first reported"',
  "  findings {",
  "    text id PK",
  "    text scan_id FK",
  "    int number",
  "    text title",
  "  }",
].join("\n");

/**
 * OKLab lightness of a computed `rgb()`/`rgba()` colour, the unit the surface
 * ladder's 0.05 floor is stated in.
 */
function oklabLightness(color: string): number {
  const [r, g, b] = color.match(/\d+(\.\d+)?/g)!.slice(0, 3).map(channel => {
    const value = Number(channel) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
}

for (const theme of ["light", "dark"]) {
  test(`${theme} every layer of a Mermaid fence sits a ladder step off the one beneath it`, async ({ page }) => {
    await page.addInitScript(value => {
      localStorage.setItem("theme", value);
      localStorage.setItem("grove.onboarding.seen", "true");
    }, theme);
    await page.route("**/api/grove/workspaces/*/sessions/*/turns*", async route => {
      const payload: SessionDetailView = await (await route.fetch()).json();
      payload.turns = [{ user_text: "Draw both", started_at: null, entries: [
        { role: "assistant", text: `\`\`\`mermaid\n${DIAGRAM}\n\`\`\`\n\n\`\`\`mermaid\n${ENTITY_DIAGRAM}\n\`\`\``, question: null, file_edit: null, todo: null },
      ] }];
      await route.fulfill({ json: payload });
    });
    await page.goto(`/w/${FIXTURE_WORKSPACES[0].id}`);
    await page.getByRole("tab", { name: "Transcript", exact: true }).click();

    const [flowchart, entities] = [0, 1].map(index => page.locator('[data-slot="mermaid-diagram"]').nth(index));
    await expect(flowchart.locator("g.cluster").first()).toBeVisible();
    await expect(entities.locator(".row-rect-even").first()).toBeAttached();

    const fill = (scope: ReturnType<typeof page.locator>, selector: string) =>
      scope.locator(selector).first().evaluate(el => getComputedStyle(el).fill);
    const pageColor = await page.locator(".aui-md").first().evaluate(el => {
      let node: Element | null = el;
      while (node) {
        const color = getComputedStyle(node).backgroundColor;
        if (color !== "rgba(0, 0, 0, 0)") return color;
        node = node.parentElement;
      }
      return "rgb(255, 255, 255)";
    });
    const well = await flowchart.evaluate(el => getComputedStyle(el).backgroundColor);
    const lightness = {
      page: oklabLightness(pageColor),
      well: oklabLightness(well),
      cluster: oklabLightness(await fill(flowchart, "g.cluster rect")),
      node: oklabLightness(await fill(flowchart, "g.node rect")),
      entityHeader: oklabLightness(await fill(entities, "g.node path.outer-path, g.node .outer-path path")),
      rowOdd: oklabLightness(await fill(entities, ".row-rect-odd path")),
      rowEven: oklabLightness(await fill(entities, ".row-rect-even path")),
    };
    const adjacentLayers: [keyof typeof lightness, keyof typeof lightness][] = [
      ["page", "well"],
      ["well", "cluster"],
      ["cluster", "node"],
      ["well", "rowEven"],
      ["rowEven", "rowOdd"],
      ["rowOdd", "entityHeader"],
    ];
    for (const [beneath, above] of adjacentLayers) {
      expect(Math.abs(lightness[above] - lightness[beneath]), `${beneath} → ${above}`).toBeGreaterThanOrEqual(0.02);
    }
    expect(lightness.entityHeader - lightness.rowEven, "header → even row").toBeGreaterThanOrEqual(0.05);
    await page.locator(".aui-md").first().screenshot({ path: test.info().outputPath(`mermaid-ladder-${theme}.png`) });
  });
}
