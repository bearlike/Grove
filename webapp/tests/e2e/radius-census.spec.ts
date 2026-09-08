import { expect, test } from "@playwright/test";

import { openSharpSurface } from "./sharp-surface-probe";

type RadiusMeasurement = {
  readonly tag: string;
  readonly slot: string | null;
  readonly testId: string | null;
  readonly className: string;
  readonly radii: readonly string[];
  readonly width: number;
  readonly height: number;
};

const EXPECTED_ELEMENTS = 100;
const APPROVED_RADII = new Set([0, 2.3, 3.45, 5.75]);
// The composer bar alone sits 20% past the container role (design-system
// §4.3); the census admits that value for that one slot and nothing else.
const SLOT_RADII: ReadonlyMap<string, number> = new Map([["composer-bar", 6.9]]);

function resolvedRadius(value: string, dimension: number): number {
  if (value.endsWith("%")) return (Number.parseFloat(value) / 100) * dimension;
  return Number.parseFloat(value);
}

function isApprovedRadius(
  value: string,
  measurement: RadiusMeasurement,
): boolean {
  const radius = resolvedRadius(
    value,
    Math.min(measurement.width, measurement.height),
  );
  return (
    APPROVED_RADII.has(radius) ||
    (measurement.slot !== null && SLOT_RADII.get(measurement.slot) === radius) ||
    radius >= 9_999 ||
    (value.endsWith("%") &&
      Math.abs(measurement.width - measurement.height) < 1 &&
      radius >= measurement.width / 2 - 0.01)
  );
}

function describe(measurement: RadiusMeasurement): string {
  const identity = [
    measurement.tag,
    measurement.slot && `data-slot=${measurement.slot}`,
    measurement.testId && `data-testid=${measurement.testId}`,
  ]
    .filter(Boolean)
    .join(" ");
  return `${identity}; class=${JSON.stringify(measurement.className)}; radius=${measurement.radii.join(", ")}`;
}

test.describe("workspace radius census", () => {
  for (const theme of ["light", "dark"] as const) {
    for (const tab of ["info", "controls"] as const) {
      test(`uses only the approved radius roles in ${tab}, ${theme}`, async ({ page }) => {
        await openSharpSurface(page, tab, 540, theme);

      const measurements = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>("*")]
          .filter((element) => {
            const style = getComputedStyle(element);
            return (
              element.getClientRects().length > 0 &&
              style.display !== "none" &&
              style.visibility !== "hidden"
            );
          })
          .map((element) => {
            const style = getComputedStyle(element);
            const box = element.getBoundingClientRect();
            return {
              tag: element.tagName.toLowerCase(),
              slot: element.dataset.slot ?? null,
              testId: element.dataset.testid ?? null,
              className: element.getAttribute("class") ?? "",
              radii: [
                style.borderTopLeftRadius,
                style.borderTopRightRadius,
                style.borderBottomRightRadius,
                style.borderBottomLeftRadius,
              ],
              width: box.width,
              height: box.height,
            };
          }),
      );

      expect(measurements.length, "the census must inspect a live workspace surface").toBeGreaterThanOrEqual(
        EXPECTED_ELEMENTS,
      );

      const offenders = measurements.filter((measurement) =>
        measurement.radii.some((radius) => !isApprovedRadius(radius, measurement)),
      );
        expect(offenders.map(describe).join("\n")).toBe("");
      });
    }
  }
});
