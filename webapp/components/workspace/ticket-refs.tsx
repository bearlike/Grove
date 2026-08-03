import Link from "next/link";
import { ArrowRight, CircleDot, GitMerge, GitPullRequest, GitPullRequestClosed } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import { prState, prStateVar, PR_STATE_LABEL, type PrState } from "@/lib/grove/status-tokens";
import type { TicketRef } from "@/lib/grove/types";

const PR_ICON: Record<PrState, LucideIcon> = {
  open: GitPullRequest,
  merged: GitMerge,
  closed: GitPullRequestClosed,
};

/** `#42` for a bare numeric key (GitHub/Gitea); a Linear key (`ENG-123`) is
 *  already its own canonical label, so it's shown verbatim. */
function ticketLabel(t: TicketRef): string {
  return t.provider === "linear" ? t.id : `#${t.id}`;
}

/**
 * The linked-refs row — the workspace's INPUT (issue(s)) and its OUTCOME (a
 * PR), each independently visible and independently clickable.
 *
 * Renders whenever ANY ref exists — an issue-only workspace (most of a
 * workspace's life, since an orchestrator attaches the issue at create and
 * the PR appears near the end) must show the same linkage the CLI
 * (`grove tickets list`), MCP, and HTTP all show. "Absence is not a state"
 * holds in its real form: zero refs renders nothing at all.
 *
 * The hue budget the original ruling protected is untouched. Several issues
 * typically resolve to ONE PR, so this reads as INPUT → OUTCOME rather than
 * two rival chip kinds: issue numbers stay plain muted text (context, no color
 * earned) and the arrow only appears when there is something to point AT; the
 * PR wears the row's one colored, actionable token, because its
 * open/merged/closed state is the most useful thing here once a PR exists.
 *
 * Deliberately placed directly under the HEADER (ahead of the "happening
 * now" context line), beside the `PhaseBadge` it usually correlates with:
 * a phase reaching delivering/done is what a PR existing already implies,
 * so this row is the payoff that redundancy sets up — it names the OUTCOME
 * once, here, rather than repeating "done"-ness a second time.
 */
export function TicketLinkage({
  refs,
  className,
}: {
  refs: TicketRef[];
  className?: string;
}) {
  const prs = refs.filter((r) => r.kind === "pull_request");
  const issues = refs.filter((r) => r.kind !== "pull_request");
  if (prs.length === 0 && issues.length === 0) return null;

  return (
    <div
      data-testid="ticket-linkage"
      className={cn("flex min-w-0 items-center gap-1.5 text-[11px] leading-none", className)}
    >
      {issues.length > 0 && (
        <>
          <span className="flex min-w-0 items-center gap-1 truncate text-muted-foreground">
            <CircleDot aria-hidden className="size-3 shrink-0" />
            <span className="truncate">
              {issues.map((issue, i) => (
                <TicketAnchor key={`${issue.provider}-${issue.id}`} ticket={issue} separator={i > 0} />
              ))}
            </span>
          </span>
          {prs.length > 0 && (
            <ArrowRight aria-hidden className="size-3 shrink-0 text-muted-foreground/50" />
          )}
        </>
      )}
      {prs.length > 0 && (
      <span className="flex min-w-0 items-center gap-1.5">
        {prs.map((pr) => {
          const state = prState(pr.status);
          const Icon = PR_ICON[state];
          const label = ticketLabel(pr);
          const title = `${pr.title ? `${pr.title} — ` : ""}${label} (${PR_STATE_LABEL[state]})`;
          const body = (
            <span
              className="inline-flex items-center gap-1 font-medium"
              style={{ color: prStateVar(state) }}
            >
              <Icon aria-hidden className="size-3 shrink-0" />
              <span className="truncate">{label}</span>
            </span>
          );
          return pr.url ? (
            <Link
              key={`${pr.provider}-${pr.id}`}
              href={pr.url}
              target="_blank"
              rel="noopener noreferrer"
              title={title}
              aria-label={`pull request ${label}, ${PR_STATE_LABEL[state]}${pr.title ? `: ${pr.title}` : ""}`}
              className="min-w-0 rounded-sm hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              {body}
            </Link>
          ) : (
            <span key={`${pr.provider}-${pr.id}`} title={title}>
              {body}
            </span>
          );
        })}
      </span>
      )}
    </div>
  );
}

/** One issue reference, comma-joined ahead of the arrow — a link when the
 *  wire gave us a URL, plain muted text otherwise (never fake a click target). */
function TicketAnchor({ ticket, separator }: { ticket: TicketRef; separator: boolean }) {
  const label = ticketLabel(ticket);
  const title = ticket.title ? `${label} — ${ticket.title}` : label;
  return (
    <>
      {separator && <span aria-hidden>, </span>}
      {ticket.url ? (
        <Link
          href={ticket.url}
          target="_blank"
          rel="noopener noreferrer"
          title={title}
          aria-label={`issue ${title}`}
          className="rounded-sm hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          {label}
        </Link>
      ) : (
        <span title={title}>{label}</span>
      )}
    </>
  );
}
