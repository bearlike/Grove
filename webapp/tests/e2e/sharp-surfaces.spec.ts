import { expect, test } from "@playwright/test";

import { dp } from "./density";
import { barInset, captureSharpSurface, openSharpSurface } from "./sharp-surface-probe";

/**
 * Tab labels sit one ramp step ABOVE the metadata floor — `text-sm` at the
 * density root — because they are the app's primary navigation. They were
 * pinned at a flat 12px held by an inline override, which measured out at the
 * same size as the shell title above them and larger than the transcript prose
 * beside them. See `shell-band-heights.spec.ts` for the full reasoning.
 */
const ACTUAL_TAB_FLOOR = dp(13);

type Rgb = readonly [number, number, number];

function linear(channel: number): number {
  const normalized = channel / 255;
  return normalized <= 0.04045
    ? normalized / 12.92
    : Math.pow((normalized + 0.055) / 1.055, 2.4);
}

function luminance([red, green, blue]: Rgb): number {
  return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue);
}

function contrast(left: Rgb, right: Rgb): number {
  const [lighter, darker] = [luminance(left), luminance(right)].sort(
    (a, b) => b - a,
  );
  return (lighter + 0.05) / (darker + 0.05);
}

test.describe("sharp surfaces", () => {
  test("keeps the nested pane strip and work strip line-sized and readable", async ({
    page,
  }) => {
    await openSharpSurface(page, "info", 540, "light");

    const paneTabs = page.getByRole("tab", {
      name: /^(Transcript|Work|Split)$/,
    });
    const workTabs = page.locator("[data-testid^='work-panel-tab-']");
    const paneList = page.locator("[aria-label='Workspace panes']");
    const workList = page
      .getByTestId("work-panel")
      .locator('[data-slot="tabs-list"]');

    await expect(paneList).toHaveAttribute("data-variant", "line");
    await expect(workList).toHaveAttribute("data-variant", "line");
    await expect(paneTabs).toHaveCount(3);
    await expect(workTabs).toHaveCount(5);

    const geometry = await page.evaluate(() => {
      const header = document.querySelector<HTMLElement>(
        "[data-testid='shell-header']",
      );
      const strip = document.querySelector<HTMLElement>(
        "[data-testid='work-panel'] [data-slot='tabs-list']",
      );
      const panes = [
        ...document.querySelectorAll<HTMLElement>(
          "[aria-label='Workspace panes'] [role='tab']",
        ),
      ];
      const work = [
        ...document.querySelectorAll<HTMLElement>(
          "[data-testid='work-panel'] [role='tab']",
        ),
      ];
      if (!header || !strip) throw new Error("workspace chrome is absent");
      const headerBox = header.getBoundingClientRect();
      const paneList = document.querySelector<HTMLElement>(
        "[aria-label='Workspace panes']",
      );
      if (!paneList) throw new Error("workspace pane strip is absent");
      const paneBox = paneList.getBoundingClientRect();
      return {
        paneInsideHeader:
          paneBox.y >= headerBox.y &&
          paneBox.y + paneBox.height <= headerBox.y + headerBox.height,
        paneFonts: panes.map((element) =>
          parseFloat(getComputedStyle(element).fontSize),
        ),
        workFonts: work.map((element) =>
          parseFloat(getComputedStyle(element).fontSize),
        ),
        paneIcons: panes.map(
          (element) => element.querySelector("svg") !== null,
        ),
        workIcons: work.map((element) => element.querySelector("svg") !== null),
      };
    });

    expect(geometry.paneInsideHeader).toBe(true);
    expect(geometry.paneFonts.every((size) => size >= ACTUAL_TAB_FLOOR)).toBe(
      true,
    );
    expect(geometry.workFonts.every((size) => size >= ACTUAL_TAB_FLOOR)).toBe(
      true,
    );
    expect(geometry.paneIcons.every(Boolean)).toBe(true);
    expect(geometry.workIcons.every(Boolean)).toBe(true);
  });

  test("uses the header gradient only for headings and keeps its text and icon contrast", async ({
    page,
  }) => {
    for (const theme of ["light", "dark"] as const) {
      await openSharpSurface(page, "info", 540, theme);
      const headers = page.locator(
        "[data-testid='info-tab'] [data-slot='card-header']",
      );
      const regions = page.locator(
        "[data-testid='info-tab'] [data-testid='checklist-meter']",
      );

      await expect(headers.first()).toBeVisible();
      const measurement = await headers.first().evaluate((header) => {
        const headerElement = header as HTMLElement;
        const title = headerElement.querySelector<HTMLElement>(
          "[data-slot='card-title']",
        );
        const icon = headerElement.querySelector<HTMLElement>(
          "[data-slot='card-title'] > span",
        );
        if (!title || !icon) throw new Error("header title or icon is absent");
        const canvas = document.createElement("canvas").getContext("2d")!;
        const rgb = (value: string): [number, number, number] => {
          canvas.clearRect(0, 0, 1, 1);
          canvas.fillStyle = value;
          canvas.fillRect(0, 0, 1, 1);
          const [r, g, b] = canvas.getImageData(0, 0, 1, 1).data;
          return [r!, g!, b!];
        };
        return {
          image: getComputedStyle(headerElement).backgroundImage,
          title: rgb(getComputedStyle(title).color),
          icon: rgb(getComputedStyle(icon).color),
          // The bottom of the vertical band is its specified darker stop.
          background: rgb(getComputedStyle(headerElement).getPropertyValue("--surface-header-end")),
        };
      });

      expect(measurement.image).toContain("linear-gradient");
      const background = measurement.background;
      expect(
        contrast(measurement.title, background),
      ).toBeGreaterThanOrEqual(4.5);
      expect(
        contrast(measurement.icon, background),
      ).toBeGreaterThanOrEqual(3);
      await expect(regions.first()).toHaveCSS("background-image", "none");
    }
  });

  test("falls back to the system canvas in forced colours", async ({
    page,
  }) => {
    await openSharpSurface(page, "info", 540, "light");
    await page.emulateMedia({ forcedColors: "active" });
    const header = page
      .locator("[data-testid='info-tab'] [data-slot='card-header']")
      .first();

    await expect(header).toHaveCSS("background-image", "none");
    await expect(header).toHaveCSS("background-color", "rgb(255, 255, 255)");
    await page.emulateMedia({ forcedColors: "none" });
  });

  test("keeps the required region lift honest rather than claiming the unavailable full rung", async ({
    page,
  }) => {
    for (const theme of ["light", "dark"] as const) {
      await openSharpSurface(page, "info", 1058, theme);
      const colours = await page.evaluate(() => {
        const card = document.querySelector<HTMLElement>(
          "[data-testid='info-tab'] [data-slot='card']",
        );
        const region = document.querySelector<HTMLElement>(
          "[data-testid='info-tab'] [data-testid='checklist-meter']",
        );
        if (!card || !region)
          throw new Error("card body or checklist region is absent");
        const canvas = document.createElement("canvas").getContext("2d")!;
        const paint = (layers: string[]): [number, number, number] => {
          canvas.clearRect(0, 0, 1, 1);
          for (const fill of layers) { canvas.fillStyle = fill; canvas.fillRect(0, 0, 1, 1); }
          const [r, g, b] = canvas.getImageData(0, 0, 1, 1).data;
          return [r!, g!, b!];
        };
        const body = getComputedStyle(card).backgroundColor;
        return { card: paint([body]), region: paint([body, getComputedStyle(region).backgroundColor]) };
      });

      // Regions retain their alpha wash while the card body rises to the
      // raised rung, so their relative luminance intentionally reverses by theme.
      expect(luminance(colours.region)).not.toBeCloseTo(
        luminance(colours.card),
        3,
      );
    }
  });

  test("assigns the measured outer, control, and inner radius roles", async ({
    page,
  }) => {
    await openSharpSurface(page, "info", 1058, "light");
    const radii = await page.evaluate(() => {
      const outer = document.querySelector<HTMLElement>(
        "[data-testid='info-tab'] [data-slot='card']",
      );
      const inner = document.querySelector<HTMLElement>(
        "[data-testid='checklist-meter']",
      );
      const control = document.querySelector<HTMLElement>(
        "[aria-label='Workspace panes'] [role='tab']",
      );
      if (!outer || !inner || !control) {
        throw new Error("a radius-role fixture is absent");
      }
      return {
        outer: getComputedStyle(outer).borderTopLeftRadius,
        inner: getComputedStyle(inner).borderTopLeftRadius,
        control: getComputedStyle(control).borderTopLeftRadius,
      };
    });

    expect(radii.outer).toBe("5.75px");
    expect(radii.control).toBe("3.45px");
    expect(radii.inner).toBe("2.3px");
  });

  test("keeps controls at their role and the composer bar at the vendor's radius", async ({ page }) => {
    await openSharpSurface(page, "info", 1058, "light");
    const send = page.locator(".aui-composer-send");
    // assistant-ui's own shape: send and attach are circles, not rounded squares.
    const round = async (selector: string) =>
      page.locator(selector).first().evaluate((el) => {
        const box = el.getBoundingClientRect();
        return Number.parseFloat(getComputedStyle(el).borderTopLeftRadius) >= Math.min(box.width, box.height) / 2;
      });
    expect(await round(".aui-composer-send")).toBe(true);
    expect(await round('[data-slot="composer-attach"]')).toBe(true);
    await expect(page.locator('[data-slot="composer-bar"]')).toHaveCSS("border-top-left-radius", "24px");
    const button = await send.boundingBox();
    const shell = await page.locator('[data-slot="composer-bar"]').boundingBox();
    expect(button).not.toBeNull();
    expect(shell).not.toBeNull();
    expect(button!.width).toBeCloseTo(28, 0);
    expect(button!.height).toBeCloseTo(28, 0);
    // Send sits IN the bar's corner: its own padding plus its border, the
      // same on both edges — the vendored geometry, not a Grove constant.
      const inset = await barInset(page);
      expect(shell!.x + shell!.width - button!.x - button!.width).toBeCloseTo(inset, 0);
    expect(shell!.y + shell!.height - button!.y - button!.height).toBeCloseTo(inset, 0);
  });

  /**
   * A 300px pane used to prove itself navigable by SCROLLING the strip, and
   * that expectation is now inverted: the row hands its labels back
   * (`data-label-mode`) until the whole census fits, so a horizontal scroller
   * here is the defect rather than the accommodation. Scrolling was never
   * reaching — a tool you have to find by dragging a strip sideways is as good
   * as absent on a phone. The label-mode ladder itself is owned by
   * `workspace-tabs.spec.ts`; what stays here is that the glyphs survive.
   */
  test("keeps a 300px work pane navigable by fitting every tab rather than clipping glyphs", async ({
    page,
  }) => {
    await openSharpSurface(page, "info", 300, "dark");
    const panel = page.getByTestId("work-panel");
    const list = panel.locator('[data-slot="tabs-list"]');

    await expect(panel).toBeVisible();
    await expect
      .poll(() =>
        panel.evaluate((element) => element.getBoundingClientRect().width),
      )
      .toBeLessThanOrEqual(300);
    await expect
      .poll(() =>
        list.evaluate((element) => element.scrollWidth - element.clientWidth),
      )
      .toBeLessThanOrEqual(1);

    const tabState = await list.evaluate((element) =>
      [...element.querySelectorAll<HTMLElement>("[role='tab']")].map((tab) => {
        const box = tab.getBoundingClientRect();
        const icon = tab
          .querySelector<SVGElement>("svg")
          ?.getBoundingClientRect();
        return {
          name: tab.textContent,
          scrollWidth: tab.scrollWidth,
          clientWidth: tab.clientWidth,
          iconWidth: icon?.width ?? 0,
          iconHeight: icon?.height ?? 0,
          tabWidth: box.width,
        };
      }),
    );

    expect(tabState).toHaveLength(5);
    for (const tab of tabState) {
      expect(tab.name).not.toBe("");
      expect(tab.scrollWidth - tab.clientWidth, tab.name).toBeLessThanOrEqual(
        1,
      );
      expect(tab.iconWidth, tab.name).toBeGreaterThan(0);
      expect(tab.iconHeight, tab.name).toBeGreaterThan(0);
      expect(tab.tabWidth, tab.name).toBeGreaterThan(0);
    }
  });

  test("captures the approved Info and Controls evidence at both review widths and themes", async ({
    page,
  }, testInfo) => {
    for (const theme of ["light", "dark"] as const) {
      for (const width of [540, 300] as const) {
        for (const tab of ["info", "controls"] as const) {
          await openSharpSurface(page, tab, width, theme);
          await captureSharpSurface(page, testInfo, tab, width, theme);
        }
      }
    }
  });
});
