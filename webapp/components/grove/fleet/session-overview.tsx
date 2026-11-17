import type { ReactNode } from "react";
import { CircleDashedIcon } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { duration } from "@/components/grove/duration";
import {
  formatContextWindow,
  formatTokenCount,
} from "@/lib/grove/adapters/context";
import { baseBranchOf } from "@/lib/grove/adapters/branch";
import type { WorkspaceActivityView } from "@/lib/grove/api";
import { cn } from "@/lib/utils";
import {
  compareTicketRefs,
  nativeFacts,
  rollupFormula,
  sessionClocks,
  ticketGlyph,
  ticketIdLabel,
  ticketPhaseKey,
  ticketPhases,
  ticketRollup,
  ticketState,
  ticketStateColour,
} from "../workspace/selectors";
import { agentStateOf } from "./filter";
import {
  agentGlyph,
  agentLabel,
  PHASE_BLOCKED_ICON,
  phaseGlyph,
  phaseLabel,
} from "./tokens";

const MAX_TICKETS = 3;
const USD = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
const NOT_REPORTED = "Not reported";

/** One label/value cell; the shared grid owns every section's geometry. */
function Field({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: ReactNode;
  wide?: boolean;
}): ReactNode {
  return (
    <div
      className={cn("session-overview-field", wide && "session-overview-wide")}
    >
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** A phase is text and a mark, never an action or a tracker state. */
function Phase({
  phase,
  blocked,
}: {
  phase: NonNullable<WorkspaceActivityView["phase"]>["phase"];
  blocked: boolean;
}): ReactNode {
  const Icon = blocked ? PHASE_BLOCKED_ICON : phaseGlyph(phase);
  return (
    <span
      className={cn(
        "session-overview-mark",
        blocked
          ? "text-warning"
          : phase === "handoff"
            ? "text-success"
            : "text-content-secondary",
      )}
    >
      <Icon aria-hidden className="size-3 shrink-0" />
      {phaseLabel(phase)}
      {blocked ? " · Blocked" : null}
    </span>
  );
}

/** A no-scroll preview over the existing fleet snapshot; full reads belong to Info. */
export function SessionOverview({
  workspace,
  unread = false,
}: {
  workspace: WorkspaceActivityView;
  unread?: boolean;
}): ReactNode {
  const { state, phase } = workspace;
  const primary = workspace.sessions[0]?.activity;
  const clocks = sessionClocks(workspace);
  const native = nativeFacts(workspace);
  const context = primary?.context
    ? formatContextWindow(primary.context.used, primary.context.size)
    : null;
  const agent = agentStateOf(workspace);
  const AgentIcon = agentGlyph(agent);
  const claims = ticketPhases(phase);
  const refs = state.ticket_refs;
  const rollup = ticketRollup(refs, phase);
  // Reuse the Info table's PR-first ordering so a late-attached PR remains visible.
  const preview = [...refs]
    .sort((a, b) =>
      compareTicketRefs(
        a,
        claims.get(ticketPhaseKey(a)) ?? null,
        b,
        claims.get(ticketPhaseKey(b)) ?? null,
      ),
    )
    .slice(0, MAX_TICKETS);
  const commit = workspace.recent_commits[0];
  const branch = workspace.branch || state.branch;
  const baseBranch = baseBranchOf({ branch, base_branch: state.base_branch });
  const fleet = workspace.fleet;
  const time = (value: number | null | undefined): string =>
    value == null ? NOT_REPORTED : duration(value);
  const tokens = (value: number | null | undefined): string =>
    value == null ? NOT_REPORTED : formatTokenCount(value);

  return (
    <div className="session-overview" data-testid="session-overview">
      <section aria-label="Session status">
        <dl className="session-overview-fields">
          <Field label="Status">
            <span
              className={cn(
                "session-overview-mark",
                workspace.needs_attention && "text-destructive",
              )}
            >
              <AgentIcon aria-hidden className="size-3 shrink-0" />
              {agentLabel(agent)}
              {unread ? (
                <span className="session-overview-count">· Unread</span>
              ) : null}
            </span>
          </Field>
          <Field label="Phase">
            {phase ? (
              <Phase phase={phase.phase} blocked={phase.blocked} />
            ) : (
              NOT_REPORTED
            )}
          </Field>
          <Field label="Note" wide>
            <span className="session-overview-note">
              {phase?.note?.trim() || "No phase note reported"}
            </span>
          </Field>
          <Field label="Model">
            <span className="session-overview-name">
              {primary?.model || NOT_REPORTED}
            </span>
          </Field>
          <Field label="Subagents">
            {fleet
              ? `${fleet.active} of ${fleet.total} active`
              : primary
                ? `${primary.active_subagents} active`
                : NOT_REPORTED}
          </Field>
          <Field label="Tool">{time(clocks?.tool_ms)}</Field>
          <Field label="Wait">{time(clocks?.generation_ms)}</Field>
          <Field label="Compute">{time(clocks?.execution_ms)}</Field>
          <Field label="Clock">{time(clocks?.active_ms)}</Field>
          <Field label="Tokens">
            <span className="session-overview-name">
              {primary?.tokens_in == null && primary?.tokens_out == null
                ? NOT_REPORTED
                : `${tokens(primary?.tokens_in)} in · ${tokens(primary?.tokens_out)} out`}
            </span>
          </Field>
          <Field label="Cost">
            {native?.cost_usd == null
              ? NOT_REPORTED
              : USD.format(native.cost_usd)}
          </Field>
          <Field label="Context" wide>
            {context
              ? `${context.percent}% · ${context.used} of ${context.size} tokens`
              : primary?.context_unavailable_reason === "stale_native_worker"
                ? "Unavailable · worker needs respawn"
                : NOT_REPORTED}
          </Field>
        </dl>
      </section>
      <Separator />
      <section aria-label="Attached tickets">
        {rollup ? (
          <>
            <dl className="session-overview-fields">
              <Field label="Progress" wide>
                <span className="session-overview-progress">
                  <Progress
                    value={Math.min(100, rollup.fraction * 100)}
                    aria-label="Average ticket phase progress"
                    aria-valuenow={rollup.fraction * 100}
                  />
                  <span
                    className="shrink-0 underline decoration-dashed underline-offset-2"
                    aria-description={rollupFormula(rollup)}
                  >
                    {Math.round(rollup.fraction * 100)}%
                  </span>
                  <span className="session-overview-count">
                    {refs.length} {refs.length === 1 ? "ticket" : "tickets"}
                  </span>
                </span>
              </Field>
              <Field label="Done" wide>
                {rollup.done} of {rollup.total} at handoff · {rollup.unreported}{" "}
                unreported · {rollup.blocked} blocked
              </Field>
            </dl>
            <table className="session-overview-tickets">
              <tbody>
                {preview.map((ref) => {
                  const key = ticketPhaseKey(ref);
                  const claim = claims.get(key);
                  const ticketStatus = ticketState(
                    ref.status,
                    ref.draft,
                    ref.kind,
                  );
                  const Icon = ticketGlyph(ref.kind, ticketStatus);
                  return (
                    <tr key={key} data-testid="session-overview-ticket">
                      <td>
                        <span className="session-overview-mark">
                          <Icon
                            aria-hidden
                            className={cn(
                              "size-3 shrink-0",
                              ticketStateColour(ticketStatus),
                            )}
                          />
                          <span className="sr-only">
                            {ref.kind === "pull_request"
                              ? "Pull request"
                              : "Issue"}
                            :{" "}
                          </span>
                          <span className="session-overview-name">
                            {ticketIdLabel(ref)}{" "}
                            {ref.title ||
                              (ref.kind === "pull_request"
                                ? "Pull request"
                                : "Issue")}
                          </span>
                        </span>
                      </td>
                      <td>
                        {claim ? (
                          <Phase phase={claim.phase} blocked={claim.blocked} />
                        ) : (
                          <span className="session-overview-mark">
                            <CircleDashedIcon
                              aria-hidden
                              className="size-3 shrink-0"
                            />
                            Unreported
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {refs.length > preview.length ? (
              <p
                className="session-overview-caption"
                data-testid="session-overview-overflow"
              >
                +{refs.length - preview.length} more · full list in Info
              </p>
            ) : null}
          </>
        ) : (
          <p className="session-overview-caption">No tickets attached</p>
        )}
      </section>
      <Separator />
      <section aria-label="Worktree">
        <dl className="session-overview-fields">
          <Field label="Branch">
            <code>{branch}</code>
          </Field>
          <Field label="Base">
            {baseBranch ? <code>{baseBranch}</code> : "No separate base"}
          </Field>
          <Field label="Changes" wide>
            {workspace.dirty_files} uncommitted
            {baseBranch
              ? ` · ${workspace.base_ahead} ahead · ${workspace.base_behind} behind`
              : ""}
          </Field>
          <Field label="Commit" wide>
            {commit ? (
              <span className="session-overview-mark">
                <code>{commit.sha.slice(0, 8)}</code>
                <span className="session-overview-name">{commit.subject}</span>
              </span>
            ) : (
              "No commits reported"
            )}
          </Field>
        </dl>
      </section>
    </div>
  );
}
