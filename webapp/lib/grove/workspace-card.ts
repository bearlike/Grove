import type {
  WorkspaceStateView,
  WorkspacePeekView,
  WorkspaceStatus,
} from "./types";

/**
 * Atomic state-and-behavior wrapper for one workspace card. Immutable;
 * `withPeek` returns a new instance to keep React renders trivial.
 */
export class WorkspaceCardModel {
  readonly state: WorkspaceStateView;
  readonly peek: WorkspacePeekView | null;

  private constructor(state: WorkspaceStateView, peek: WorkspacePeekView | null) {
    this.state = state;
    this.peek = peek;
  }

  static fromState(s: WorkspaceStateView): WorkspaceCardModel {
    return new WorkspaceCardModel(s, null);
  }

  withPeek(p: WorkspacePeekView): WorkspaceCardModel {
    return new WorkspaceCardModel(this.state, p);
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
    if (!this.peek) return "—";
    const { base_ahead, base_behind, dirty_files } = this.peek;
    return `ahead ${base_ahead} · behind ${base_behind} · ${dirty_files} dirty`;
  }
}
