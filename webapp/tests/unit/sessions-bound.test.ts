import { describe, expect, it } from "vitest";

import { sessionCountLabel } from "@/components/grove/sessions/filter";

/**
 * The two bounds this line has to state, and the one it used to hide.
 *
 * `GET /sessions` slices to a `limit` (50 by default) and returns **no grand
 * total**, so on a host with several hundred sessions the page rendered exactly
 * 50 rows under the words "50 sessions" — a full stop, which reads as *this is
 * everything*. Filtering was already stated; capping was not.
 *
 * The label cannot honestly print "50 of 312" because nobody tells it 312. What
 * it can state is which END of the list it holds, and the daemon documents the
 * catalog as newest-first — so the wording pins that, not an invented total.
 */
describe("sessionCountLabel", () => {
  it("states a plain count when it is showing everything", () => {
    expect(sessionCountLabel(1, 1)).toBe("1 session");
    expect(sessionCountLabel(2, 2)).toBe("2 sessions");
  });

  it("states the narrowing when a filter is on", () => {
    expect(sessionCountLabel(3, 40)).toBe("3 of 40 sessions");
  });

  // The regression this file exists for.
  it("says which end of the list it holds when the request hit its cap", () => {
    const label = sessionCountLabel(200, 200, true);
    expect(label).toContain("200 sessions");
    expect(label).toContain("newest");
    expect(label).toContain("on this host");
  });

  it("states both bounds at once — narrowed AND capped", () => {
    const label = sessionCountLabel(7, 200, true);
    expect(label).toContain("7 of 200 sessions");
    expect(label).toContain("newest");
  });

  // An uncapped page must not imply a horizon it does not have: the wording is
  // a claim about the request, so it may never appear when nothing was cut.
  it("never mentions a horizon when nothing was cut", () => {
    expect(sessionCountLabel(40, 40)).not.toContain("newest");
    expect(sessionCountLabel(3, 40)).not.toContain("newest");
  });
});
