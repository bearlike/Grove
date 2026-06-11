"use client";

import { Component, type ReactNode } from "react";

/**
 * A render-error containment boundary. A single malformed card — an
 * out-of-contract enum from a streamed delta, a null where a value was
 * assumed — must degrade to a small placeholder, never unmount the whole
 * dashboard (React tears down the entire tree on an uncaught render throw =
 * the "it crashes everything" white-screen). Wrap each card in one of these so
 * the blast radius is one tile, and the rest of the wall keeps streaming.
 *
 * The boundary is transparent on the happy path: it renders `children`
 * directly with no wrapper element, so layout/animation refs on the parent
 * still see the real card as their direct child.
 */
export class ErrorBoundary extends Component<
  { children: ReactNode; fallback?: ReactNode; onError?: (error: unknown) => void },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown): void {
    // Best-effort surfacing — never re-raise (that defeats the boundary).
    this.props.onError?.(error);
    if (typeof console !== "undefined") console.error("card render error contained:", error);
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return this.props.fallback ?? <DefaultFallback />;
  }
}

function DefaultFallback() {
  return (
    <div
      data-testid="card-error"
      role="alert"
      className="rounded-lg border border-[var(--status-error)]/40 bg-card p-4 text-xs text-muted-foreground"
    >
      <span className="font-mono text-[var(--status-error)]">render error</span> — this card
      couldn&apos;t be drawn from the current data. The rest of the dashboard is unaffected.
    </div>
  );
}
