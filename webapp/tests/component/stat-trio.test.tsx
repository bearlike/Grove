import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { StatTrio } from "@/components/workspace/stat-trio";

function r(node: React.ReactNode) {
  return render(<ThemeProvider attribute="class" defaultTheme="dark">{node}</ThemeProvider>);
}

// jsdom serializes inline `color: #xxx` to `color: rgb(r, g, b)`; expectColor
// hides the conversion so the assertion stays readable in hex.
function expectColor(el: Element, hex: string) {
  const m = hex.match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
  if (!m) throw new Error(`expectColor needs full 6-digit hex, got ${hex}`);
  const rgb = `rgb(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)})`;
  const style = (el.getAttribute("style") ?? "").toLowerCase();
  expect(style).toContain(rgb);
}

describe("StatTrio", () => {
  it("renders all three stats with values", () => {
    r(<StatTrio ahead={3} behind={1} dirty={2} />);
    expect(screen.getByTestId("stat-ahead").textContent).toContain("3");
    expect(screen.getByTestId("stat-behind").textContent).toContain("1");
    expect(screen.getByTestId("stat-dirty").textContent).toContain("2");
  });

  it("zero values render in muted color", () => {
    r(<StatTrio ahead={0} behind={0} dirty={0} />);
    expectColor(screen.getByTestId("stat-ahead"), "#96938c");
  });

  it("nonzero ahead renders in ref-add green", () => {
    r(<StatTrio ahead={5} behind={0} dirty={0} />);
    expectColor(screen.getByTestId("stat-ahead"), "#99d199");
  });

  it("nonzero behind / dirty render in amber", () => {
    r(<StatTrio ahead={0} behind={2} dirty={4} />);
    expectColor(screen.getByTestId("stat-behind"), "#b8860b");
    expectColor(screen.getByTestId("stat-dirty"), "#b8860b");
  });
});
