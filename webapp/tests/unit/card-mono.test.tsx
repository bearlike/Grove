import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CardField, CardFields, CardStat } from "@/components/grove/card";

/**
 * Monospace is semantic here, and these pin the semantics rather than the look.
 *
 * The rule (§3): mono means "a literal you could retype and have it mean the
 * same thing" — a ref, a SHA, a path, an id. It is not a texture, and it is not
 * how data looks technical. Two places in this primitive had it wrong, and both
 * were invisible because each read as a reasonable local choice:
 *
 *   - `CardStat` set every figure in mono, which is where the work-panel's
 *     `+0 −0` got its treatment. The audit map described that as five separate
 *     per-tab defects; no tab styles its own figures.
 *   - `CardField`'s `mono` flag was documented as "identifiers AND timestamps"
 *     and welded `font-mono` to `tabular-nums`, so the two could not be asked
 *     for separately. A timestamp wants tabular figures and not mono.
 */
const render = (node: React.ReactNode): string => renderToStaticMarkup(node);

/** The `dd` of a one-row `CardFields`, with its classes. */
function valueTag(html: string): string {
  const match = html.match(/<dd[^>]*>/);
  if (!match) throw new Error("no value row in markup");
  return match[0];
}

describe("CardStat", () => {
  it("sets a metric in sans, because a quantity is not a literal", () => {
    const html = render(<CardStat label="dirty" value={12} />);

    expect(html).not.toContain("font-mono");
    expect(html).toContain("tabular-nums");
  });

  it("still colours a signed figure, which keeps its label as the second carrier", () => {
    // `+12` in green is legal only because "added" sits directly under it.
    const html = render(<CardStat label="added" value="+12" tone="positive" />);

    expect(html).toContain("text-success");
    expect(html).toContain("added");
  });
});

describe("CardField", () => {
  it("gives every row tabular figures, whether or not it is an identifier", () => {
    const html = render(
      <CardFields>
        <CardField label="Updated">3d ago</CardField>
      </CardFields>,
    );

    expect(valueTag(html)).toContain("tabular-nums");
    expect(valueTag(html)).not.toContain("font-mono");
  });

  it("reserves mono for the identifier case, and asks for nothing else with it", () => {
    const html = render(
      <CardFields>
        <CardField label="Branch" mono>
          feat/rail
        </CardField>
      </CardFields>,
    );

    expect(valueTag(html)).toContain("font-mono");
  });
});
