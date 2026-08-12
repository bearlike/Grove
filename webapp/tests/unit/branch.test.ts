import { describe, expect, it } from "vitest";

import { baseBranchOf } from "@/lib/grove/adapters";

/**
 * The wire has no nullable base-branch field, so absence arrives as the literal
 * string `HEAD` and every renderer that trusts the field prints a branch that
 * does not exist. These pin the reading, not the rendering: three surfaces write
 * three different sentences for `null`, and they can only stay consistent about
 * WHEN to write one if exactly one function decides it.
 */
const state = (branch: string, base: string) => ({ branch, base_branch: base });

describe("baseBranchOf", () => {
  it("returns a real base branch untouched", () => {
    expect(baseBranchOf(state("feat/rail", "main"))).toBe("main");
  });

  it("reads the HEAD sentinel as absence, not as a branch named HEAD", () => {
    // A ROOT workspace adopts the repo's live checkout, so the engine records
    // `base_branch = "HEAD"`. This is the defect the helper exists for.
    expect(baseBranchOf(state("main", "HEAD"))).toBeNull();
  });

  it("reads a branch compared against itself as absence", () => {
    expect(baseBranchOf(state("main", "main"))).toBeNull();
  });

  it("reads an empty or blank base as absence rather than as an empty name", () => {
    expect(baseBranchOf(state("main", ""))).toBeNull();
    expect(baseBranchOf(state("main", "   "))).toBeNull();
  });

  it("does not rewrite a real branch that merely starts with HEAD", () => {
    // The reason this check lives here and not inside `BranchLabel`: a user may
    // legitimately name a branch `HEAD-something`, and a shared atom that
    // rewrote its own input would be wrong for every other caller.
    expect(baseBranchOf(state("feat/x", "HEAD-of-line"))).toBe("HEAD-of-line");
    expect(baseBranchOf(state("feat/x", "head"))).toBe("head");
  });

  it("trims, so whitespace never becomes part of a ref", () => {
    expect(baseBranchOf(state("feat/x", "  main  "))).toBe("main");
    expect(baseBranchOf(state("  main  ", "main"))).toBeNull();
  });
});
