import { readFileSync } from "node:fs";
import { ActivityIcon, GaugeIcon } from "lucide-react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CardCell, CardRegion } from "@/components/grove/card";
import { Explain } from "@/components/grove/glossary";

/**
 * THE INFO TAB'S FACTS ARE ENCLOSURES, NOT CONTROLS OR CROPS.
 *
 * In a docked panel a metric needs enough structure to scan without looking
 * clickable, and absence must not impersonate a small value. These static
 * renders pin the browser-visible contract: geometry and vocabulary, rather
 * than the implementation choices that compose it.
 */
const infoTabSource = readFileSync(
  new URL("../../components/grove/workspace/info-tab.tsx", import.meta.url),
  "utf8",
);
const ticketRefsSource = readFileSync(
  new URL("../../components/grove/workspace/ticket-refs.tsx", import.meta.url),
  "utf8",
);
const css = readFileSync(new URL("../../app/globals.css", import.meta.url), "utf8");

function sectionBefore(source: string, title: string): string {
  const beforeTitle = source.slice(0, source.indexOf(`title=\"${title}\"`));
  return beforeTitle.slice(beforeTitle.lastIndexOf("<SectionCard"));
}

function tag(html: string, testId: string): string {
  const match = html.match(new RegExp(`<[^>]*data-testid="${testId}"[^>]*>`));
  if (!match) throw new Error(`no ${testId} in markup`);
  return match[0];
}

function cell(props: Partial<React.ComponentProps<typeof CardCell>> = {}): string {
  return renderToStaticMarkup(
    <CardCell
      data-testid="metric"
      icon={<GaugeIcon aria-hidden />}
      label="Tool calls"
      value="1,248"
      {...props}
    />,
  );
}

describe("CardRegion", () => {
  it("is a bounded visual enclosure, never a height crop for prose", () => {
    const html = renderToStaticMarkup(
      <CardRegion data-testid="region">A report long enough to wrap instead of disappear.</CardRegion>,
    );
    const region = tag(html, "region");

    expect(region).toContain("overflow-hidden");
    expect(region).toContain("bg-muted/30");
    expect(region).toContain("p-2.5");
    expect(region).not.toMatch(/\b(?:h-|min-h-|max-h-)/);
  });
});

describe("CardCell", () => {
  it("uses a minimum reference height so a translated label can grow", () => {
    const metric = tag(cell(), "metric");

    const classes = metric.match(/class="([^"]*)"/)?.[1].split(/\s+/) ?? [];

    expect(classes).toContain("min-h-16");
    expect(classes).not.toContain("h-16");
  });

  it("pins the compact metric geometry without shrinking its type below the floor", () => {
    // The ramp is rebased: `text-xl` is 18px/24px here, so Tailwind's familiar
    // name is wrong for this 20px/26px figure. Assert the rendered step AND its
    // local definition, so either a component regression or a quiet ramp move
    // turns this red.
    const html = cell();
    const metric = tag(html, "metric");

    expect(metric).toContain("p-2.5");
    expect(html).toContain("size-4");
    expect(html).toContain("gap-1");
    expect(html).toContain("text-xs leading-4");
    expect(html).toContain("text-2xl");
    expect(css).toMatch(/--text-2xl:\s*1\.25rem/);
    expect(css).toMatch(/--text-2xl--line-height:\s*1\.625rem/);
    expect(html).not.toMatch(/text-\[(?:[0-9]|1[01])px\]/);
  });

  it("renders an absent measurement visibly quieter than a real figure", () => {
    const figure = cell();
    const absence = cell({ value: "Not measured", muted: true });

    expect(figure).toContain("text-2xl text-content-primary");
    expect(absence).toContain("text-xs leading-4 text-content-tertiary");
    expect(absence).not.toContain("text-2xl text-content-primary");
  });

  it("attributes derived arithmetic with a dashed value underline", () => {
    const html = cell({ derived: true, title: "Sum of reported per-message counts" });
    expect(html).toContain('data-derived="true"');
    expect(html).toContain("decoration-dashed");
    expect(html).toContain('title="Sum of reported per-message counts"');
  });

  it("keeps glossary definitions dotted so provenance and explanation remain distinct", () => {
    const html = renderToStaticMarkup(
      <CardCell
        data-testid="glossary-metric"
        icon={<ActivityIcon aria-hidden />}
        label={<Explain term="compute_time">Compute time</Explain>}
        value="42m"
        derived
        title="Computed from every agent interval"
      />,
    );

    expect(html).toContain("decoration-dashed");
    expect(html).toContain("decoration-dotted");
    expect(html).not.toContain("decoration-dashed decoration-dotted");
  });

  it("offers no button-like or hover affordance for a fact", () => {
    const html = cell();

    expect(html).not.toMatch(/<(?:button|a)\b/);
    expect(html).not.toContain("tabindex=");
    expect(html).not.toContain("cursor-pointer");
    expect(html).not.toContain("hover:");
  });
});

describe("the composed info cards", () => {
  it("keeps Task, Activity, and Tickets as title-only sections", () => {
    // These components need live data and a query client to render their full
    // states. This source census pins the JSX boundary where a subtitle band can
    // return; it does not mistake a comment or a child component for that prop.
    for (const [source, title] of [
      [infoTabSource, "Task"],
      [infoTabSource, "Activity"],
      [ticketRefsSource, "Tickets"],
    ] as const) {
      expect(sectionBefore(source, title), title).not.toContain("description=");
    }
  });

  it("keeps labels sentence case rather than using uppercase as hierarchy", () => {
    const sources = [
      readFileSync(new URL("../../components/grove/card.tsx", import.meta.url), "utf8"),
      infoTabSource,
      readFileSync(new URL("../../components/grove/workspace/phase-meter.tsx", import.meta.url), "utf8"),
      readFileSync(new URL("../../components/grove/workspace/ticket-refs.tsx", import.meta.url), "utf8"),
    ];

    for (const source of sources) {
      // `uppercase` in prose is not a visual hierarchy. Restricting the census
      // to quoted class lists makes the failure point a rendered class.
      const classLists = [...source.matchAll(/className="([^"]*)"/g)].map((match) => match[1]!);
      expect(classLists.filter((classes) => /\buppercase\b/.test(classes))).toEqual([]);
      expect(classLists.filter((classes) => /\btext-\[/.test(classes))).toEqual([]);
    }
  });
});
