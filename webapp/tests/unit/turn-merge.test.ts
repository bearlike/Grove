import { describe, expect, it } from "vitest";

import { mergeTurns, turnCursor, type HeldWindow, type TurnWindow } from "@/lib/grove/adapters";
import type { SessionTurnView } from "@/lib/grove/api";

/**
 * The incremental `/turns` read, as a decision about placement.
 *
 * Every case here is a way a transcript could silently lose or duplicate a
 * turn, which is the only failure mode that matters — the byte saving is the
 * easy half and would look identical whether or not these hold.
 *
 * Every held window here is explicit about its `first_turn_index`, because
 * that is the whole point of this file: a client can hold a window that does
 * NOT start at session index 0 (the first read is a TAIL — see
 * `INITIAL_TURN_WINDOW` in `lib/grove/hooks/queries.ts`), and both `turnCursor`
 * and `mergeTurns` have to place things correctly against that.
 */

function turn(text: string): SessionTurnView {
  return { user_text: text, started_at: null, entries: [] };
}

function held(turns: readonly SessionTurnView[], first_turn_index = 0): HeldWindow {
  return { turns, first_turn_index };
}

function window(fields: Partial<TurnWindow> & Pick<TurnWindow, "turns">): TurnWindow {
  return {
    total_turns: fields.turns.length,
    first_turn_index: 0,
    incremental: true,
    ...fields,
  };
}

const HELD = held([turn("a"), turn("b"), turn("c")]);

describe("turnCursor", () => {
  it("asks from the LAST held turn, not past it — the tail is still growing", () => {
    expect(turnCursor(HELD)).toBe(2);
  });

  it("asks for no cursor when there is nothing to resume from", () => {
    expect(turnCursor(held([]))).toBeUndefined();
    expect(turnCursor(undefined)).toBeUndefined();
  });

  // (a) — the property that makes windowed reads possible at all: a held
  // window that does not start at session index 0 (a tail from
  // `INITIAL_TURN_WINDOW`, or one already widened by `loadEarlier`) must ask
  // for its cursor relative to the SESSION, not relative to its own array.
  it("is ABSOLUTE, not relative to what this window holds", () => {
    const tail = held([turn("x"), turn("y")], 1700);
    expect(turnCursor(tail)).toBe(1701); // 1700 + 2 - 1, not 2 - 1
  });
});

describe("mergeTurns", () => {
  it("replaces wholesale when the daemon did not honour the cursor", () => {
    const fresh = [turn("x"), turn("y")];
    const merge = mergeTurns(HELD, window({ turns: fresh, incremental: false }));
    expect(merge).toEqual({ kind: "replace", turns: fresh, first_turn_index: 0 });
  });

  it("re-reads the tail turn in place rather than appending it twice", () => {
    // The cursor is inclusive, so the tail we already hold comes back — grown.
    const grown = turn("c (with a tool result)");
    const merge = mergeTurns(HELD, window({ turns: [grown], first_turn_index: 2, total_turns: 3 }));

    expect(merge).toEqual({
      kind: "replace",
      turns: [HELD.turns[0], HELD.turns[1], grown],
      first_turn_index: 0,
    });
  });

  // Identity-preserving, so that a future per-turn memo in `messagesFromTurns`
  // would have something to key on. It buys nothing today — that function
  // rebuilds every message whenever the array identity changes.
  it("keeps the prefix objects it already had rather than rebuilding them", () => {
    const merge = mergeTurns(HELD, window({ turns: [turn("c'")], first_turn_index: 2 }));
    const turns = (merge as { turns: readonly SessionTurnView[] }).turns;
    expect(turns[0]).toBe(HELD.turns[0]);
    expect(turns[1]).toBe(HELD.turns[1]);
  });

  it("appends genuinely new turns after the refreshed tail", () => {
    const merge = mergeTurns(
      HELD,
      window({ turns: [turn("c'"), turn("d")], first_turn_index: 2, total_turns: 4 }),
    );
    const turns = (merge as { turns: readonly SessionTurnView[] }).turns;
    expect(turns.map((t) => t.user_text)).toEqual(["a", "b", "c'", "d"]);
  });

  it("reports UNCHANGED — carrying no turns — for an empty window at the end", () => {
    const merge = mergeTurns(HELD, window({ turns: [], first_turn_index: 3, total_turns: 3 }));
    expect(merge).toEqual({ kind: "unchanged" });
    // The absence of a `turns` payload is the contract: it is what stops a
    // caller handing back a fresh-but-equal array and re-converting everything.
    expect(merge).not.toHaveProperty("turns");
  });

  it("shrinks when the session now holds fewer turns than we do", () => {
    // `after_turn == total` is an empty incremental window, not a gap.
    const merge = mergeTurns(HELD, window({ turns: [], first_turn_index: 2, total_turns: 2 }));
    expect(merge).toEqual({
      kind: "replace",
      turns: [HELD.turns[0], HELD.turns[1]],
      first_turn_index: 0,
    });
  });

  it("refuses to splice a window that starts past what we hold", () => {
    // Would leave turns 3..4 unaccounted for; a hole is worse than a refetch.
    expect(mergeTurns(HELD, window({ turns: [turn("f")], first_turn_index: 5 }))).toEqual({
      kind: "refetch",
    });
  });

  it("refuses an incremental window with nothing to place it against", () => {
    expect(mergeTurns(undefined, window({ turns: [turn("a")] }))).toEqual({ kind: "refetch" });
  });

  it("degrades to a full replace when `incremental` is ABSENT, not just false", () => {
    // The failure this pins: FastAPI ignores an unknown query param, so a
    // daemon that predates the cursor answers `after_turn=` with the WHOLE
    // session and no `incremental` field. Splicing that against the tail would
    // silently duplicate turns while every request looked successful. Absent
    // must land in the same branch as false — never in the splice.
    const whole = [turn("a"), turn("b"), turn("c")];
    const legacy = { turns: whole, total_turns: 3, first_turn_index: 0 } as unknown as TurnWindow;
    expect(mergeTurns(HELD, legacy)).toEqual({ kind: "replace", turns: whole, first_turn_index: 0 });
  });

  it("takes a non-incremental window even with nothing held — the first read", () => {
    const fresh = [turn("a")];
    expect(mergeTurns(undefined, window({ turns: fresh, incremental: false }))).toEqual({
      kind: "replace",
      turns: fresh,
      first_turn_index: 0,
    });
  });

  it("places a window that starts exactly at the end without a gap", () => {
    const merge = mergeTurns(HELD, window({ turns: [turn("d")], first_turn_index: 3 }));
    expect(merge).toEqual({
      kind: "replace",
      turns: [HELD.turns[0], HELD.turns[1], HELD.turns[2], turn("d")],
      first_turn_index: 0,
    });
  });

  // (b) — the case that motivated fixing the offset: `held` itself is a
  // WINDOW starting at an absolute index, not session turn 0 (e.g. a tail
  // from `INITIAL_TURN_WINDOW`). The cursor the client sent (per `turnCursor`)
  // is `held.first_turn_index + held.turns.length - 1`, so the daemon's
  // response starts there too — the merge must slice `held.turns` relative to
  // ITS OWN start, not treat `window.first_turn_index` as an absolute array
  // index into `held.turns`.
  it("follows a windowed held range: keeps the prefix by reference and reports the true first_turn_index", () => {
    const tail = held([turn("p"), turn("q"), turn("r")], 1700); // absolute 1700..1702
    const grownTail = turn("r (grown)");
    const merge = mergeTurns(
      tail,
      window({ turns: [grownTail], first_turn_index: 1702, total_turns: 1703 }),
    );

    expect(merge.kind).toBe("replace");
    const result = merge as { turns: readonly SessionTurnView[]; first_turn_index: number };
    expect(result.first_turn_index).toBe(1700); // NOT window.first_turn_index (1702)
    expect(result.turns).toHaveLength(3);
    expect(result.turns[0]).toBe(tail.turns[0]);
    expect(result.turns[1]).toBe(tail.turns[1]);
    expect(result.turns[2]).toBe(grownTail);
  });

  it("follows a windowed held range and appends a genuinely new tail turn", () => {
    const tail = held([turn("p"), turn("q"), turn("r")], 1700);
    const merge = mergeTurns(
      tail,
      window({ turns: [turn("r (grown)"), turn("s")], first_turn_index: 1702, total_turns: 1704 }),
    );
    const result = merge as { turns: readonly SessionTurnView[]; first_turn_index: number };
    expect(result.first_turn_index).toBe(1700);
    expect(result.turns.map((t) => t.user_text)).toEqual(["p", "q", "r (grown)", "s"]);
  });

  // (c) — a "load earlier" widen. The daemon has no prefix-only instrument
  // (only `last`, tail-anchored), so a widen re-fetches the WHOLE wider tail —
  // both the new earlier turns and the ones already held — in one
  // non-incremental response. That response is already the complete,
  // contiguous, de-duplicated answer, so placing it is a wholesale replace:
  // there is nothing left to splice, and nothing that could duplicate a turn.
  it("replaces wholesale with a widened (earlier) window — one contiguous range, no duplicates", () => {
    const tail = held([turn("e"), turn("f")], 2); // absolute 2..3
    const widened = window({
      turns: [turn("a"), turn("b"), turn("c"), turn("d"), turn("e"), turn("f")],
      first_turn_index: 0,
      total_turns: 6,
      incremental: false,
    });

    const merge = mergeTurns(tail, widened);
    expect(merge).toEqual({ kind: "replace", turns: widened.turns, first_turn_index: 0 });
    // No duplicate content, and the tail's own two turns still land last.
    const turns = (merge as { turns: readonly SessionTurnView[] }).turns;
    expect(turns.map((t) => t.user_text)).toEqual(["a", "b", "c", "d", "e", "f"]);
  });

  // (d) — non-contiguous, against a windowed (not zero-based) held range.
  it("refuses an incremental window that does not overlap a windowed held range", () => {
    const tail = held([turn("a"), turn("b")], 10); // absolute 10..11
    const merge = mergeTurns(tail, window({ turns: [turn("z")], first_turn_index: 20 }));
    expect(merge).toEqual({ kind: "refetch" });
  });

  it("refuses an incremental window that starts before a windowed held range", () => {
    // `turnCursor` never asks for this, but the function stays fail-safe
    // rather than trusting a response the request could not have produced.
    const tail = held([turn("a"), turn("b")], 10); // absolute 10..11
    const merge = mergeTurns(tail, window({ turns: [turn("z")], first_turn_index: 5 }));
    expect(merge).toEqual({ kind: "refetch" });
  });
});
