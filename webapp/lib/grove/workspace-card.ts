import type {
  WorkspaceActivityView,
  WorkspaceStateView,
  WorkspaceStatus,
} from "./types";

/** The card footer's stat trio, normalized from whichever wire shape supplied it. */
export interface CardStats {
  readonly ahead: number;
  readonly behind: number;
  readonly dirty: number;
}

/**
 * Atomic state-and-behavior wrapper for one workspace card. Immutable; the
 * static factories normalize each wire shape into one model so the card
 * component renders a single type, never a union of payloads.
 */
export class WorkspaceCardModel {
  readonly state: WorkspaceStateView;
  readonly stats: CardStats | null;

  private constructor(state: WorkspaceStateView, stats: CardStats | null) {
    this.state = state;
    this.stats = stats;
  }

  /** Bare workspace state (no git stats yet) — the footer renders placeholders. */
  static fromState(s: WorkspaceStateView): WorkspaceCardModel {
    return new WorkspaceCardModel(s, null);
  }

  /** The activity-stream shape: workspace state + git stats arrive in one view. */
  static fromActivity(w: WorkspaceActivityView): WorkspaceCardModel {
    return new WorkspaceCardModel(w.state, {
      ahead: w.base_ahead,
      behind: w.base_behind,
      dirty: w.dirty_files,
    });
  }

  get displayStatus(): WorkspaceStatus {
    return this.state.status;
  }

  get isLive(): boolean {
    return this.state.status === "active" || this.state.status === "idle";
  }

  get hasAttention(): boolean {
    return this.state.status === "orphaned" || this.state.status === "error";
  }

  get summaryLine(): string {
    if (!this.stats) return "—";
    const { ahead, behind, dirty } = this.stats;
    return `ahead ${ahead} · behind ${behind} · ${dirty} dirty`;
  }
}
