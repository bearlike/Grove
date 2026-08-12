"use client";

import { createContext, useContext, type PropsWithChildren, type ReactNode } from "react";

/**
 * The one mechanism the expanded tool-call tree uses at every level it has —
 * group, an individual call inside it, that call's own request/response —
 * rather than three hand-tuned components that happen to look similar.
 * `NestLevel` marks "this content is one level deeper than what contains
 * it"; nothing else in this file knows how many levels exist or what they
 * are called.
 *
 * WHY A CONTEXT AND NOT A PROP. `ToolCallPart` is mounted by assistant-ui's
 * own grouping machinery (`MessagePrimitive.GroupedParts`, ported in
 * `./thread`) with a fixed prop shape neither Grove nor this file controls —
 * there is no call-site seam to hand a depth number through, and the SAME
 * component renders a tool call whether or not a `ToolCallGroup` is its
 * parent. A context is the one way to tell a component which nesting
 * boundary it was mounted inside without assistant-ui's cooperation, and
 * every consumer here defaults to depth 0, so a standalone tool call (no
 * group above it) is unaffected.
 *
 * WHY GEOMETRY, NOT A FOURTH TYPE STEP. design-system.md §1 puts `text-xs`
 * (12px) at the floor, and the vendored group header (`ToolGroupTrigger`)
 * already renders its own label there — so the OUTERMOST row in this tree
 * already has no room left to shrink. `nestedTriggerClass` spends the one
 * step that exists (pulling a nested call's trigger off the vendored
 * `text-sm` it would otherwise inherit, which used to render LARGER than the
 * group around it — the exact inversion this file exists to fix) and every
 * level past that rides the same floor. Indentation and the connector line
 * are what keep separating levels once type has nowhere left to go, which is
 * why `NestLevel` draws both on every call, unconditionally.
 */
const NestDepthContext = createContext(0);

/** How many `NestLevel` boundaries this render is inside. 0 at the tree's own
 * top — a tool call or file edit with no group above it — where nothing here
 * has an opinion and vendored defaults stand untouched. */
export function useNestDepth(): number {
  return useContext(NestDepthContext);
}

/**
 * One recursive step down: raises the ambient depth for its children and
 * draws the geometry that marks them as nested.
 *
 * `pl-3` reuses `CardShell`'s own body padding value rather than inventing a
 * new one — an indent is a position, the same way `card.tsx`'s spacing scale
 * is a fixed set of positions, and a second value doing the same job would be
 * the thing §8 warns against. `border-border` is the same decorative-edge
 * token `CardShell`'s own `border-t` already uses; 1.4.11's 3:1 floor does
 * not reach a decorative container edge (design-system §4.6), and none is
 * claimed here — the line is a supporting cue, never the only one, which is
 * why it always renders alongside the indent rather than instead of it.
 *
 * `text-xs` is the ambient default for anything mounted here that does not
 * set its own size — the ramp's floor, applied once at the boundary instead
 * of re-declared by every child that happens to reach it today. `min-w-0` is
 * load-bearing, not decorative: a flex column's items default to
 * `min-width: auto`, and without it this wrapper would refuse to shrink
 * below its content, breaking every `truncate` already relied on one level
 * in (the tool-call target line, a file path).
 */
export function NestLevel({ children }: PropsWithChildren): ReactNode {
  const depth = useNestDepth();
  return (
    <NestDepthContext.Provider value={depth + 1}>
      <div
        data-testid="nest-level"
        data-nest-depth={depth + 1}
        className="flex min-w-0 flex-col gap-1.5 border-l border-border pl-3 text-xs"
      >
        {children}
      </div>
    </NestDepthContext.Provider>
  );
}

/**
 * The override that pulls a vendored trigger down to the floor once it is
 * nested a level deep. `undefined` at depth 0 leaves the vendored `text-sm`
 * exactly as shipped — a standalone tool call still reads one ramp step
 * below the transcript's own `text-base` prose, which is correct and needs
 * no override. See `NestLevel`'s docstring for why depth 1+ has nowhere
 * lower to go than the same floor.
 */
export function nestedTriggerClass(depth: number): string | undefined {
  return depth > 0 ? "text-xs" : undefined;
}
