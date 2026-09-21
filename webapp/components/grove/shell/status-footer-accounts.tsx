"use client";

import { AgentMark } from "@/components/grove/agent-mark";
import { QUOTA_TEXT_TONE } from "@/components/grove/shell/footer/quota-tone";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  percentLabel,
  quotaTone,
  quotaUrgent,
  type AccountFit,
  type AccountSummary,
} from "@/lib/grove/adapters/footer";

function accountReading(account: AccountSummary): React.ReactNode {
  return (
    <>
      <span className="text-content-tertiary">{account.window ?? "window"}</span>
      {" "}
      {account.percent === null ? (
        <span className="text-content-tertiary">not measured</span>
      ) : (
        <span
          className={`tabular-nums ${QUOTA_TEXT_TONE[quotaTone(account.percent)!]} ${quotaUrgent(account.percent) ? "quota-urgent" : ""}`}
          data-testid="popover-account-percent"
        >
          {percentLabel(account.percent)}%
        </span>
      )}
      {account.stale ? <span className="text-warning"> stale</span> : null}
    </>
  );
}

/**
 * The complete account list, EXPORTED so it can be rendered without a portal.
 *
 * Radix keeps `PopoverContent` closed and portalled, so a static render of
 * `AccountOverflow` emits the trigger and nothing else — a guard asserting on
 * this list through that component matches an empty string and passes for the
 * wrong reason. Found exactly that way: the first version of the brand-mark
 * test reported `[]` rather than failing on a wrong brand.
 */
export function AccountList({ accounts }: { accounts: readonly AccountSummary[] }): React.ReactNode {
  return (
    <div className="flex flex-col gap-1">
      {accounts.map((account) => (
        <div key={account.accountId} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] gap-x-1.5 text-sm">
          {/* The provider's BRAND MARK, matching the strip this popover opens
              from and every fleet row. A gauge glyph tinted by headroom said
              the same thing the percentage beside it already says, while
              leaving the one question this list exists to answer — whose
              account is this — to the text alone. The mark is the identity;
              the tone stays on the reading. */}
          <span className="flex min-w-0 items-center gap-1.5">
            <AgentMark agentName={account.provider} className="size-[1em] shrink-0" />
            <span className="min-w-0 truncate text-content-secondary" title={account.label}>
              {account.label}
            </span>
          </span>
          <span className="shrink-0 text-xs">{accountReading(account)}</span>
          <span className="col-start-1 ml-[1.5em] truncate text-xs text-content-tertiary">
            {account.provider} · {account.status}
          </span>
        </div>
      ))}
      <a href="/usage" className="mt-1 text-xs text-content-secondary underline">
        View usage
      </a>
    </div>
  );
}

/** The complete account list behind the compact subscription strip. */
export function AccountOverflow({ fit }: { fit: AccountFit }): React.ReactNode {
  if (fit.hidden.length === 0) return null;

  const hiddenCount = fit.hidden.length;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="xs"
          // A DOTTED UNDERLINE, because nothing else here says this is a
          // control. It sits in a band of plain readings, its ghost variant
          // has no resting fill or border, and a `+2 accounts` that looks
          // exactly like `22 tickets` two sections away is a click nobody
          // makes. Dotted rather than solid: solid is the app's link
          // affordance (`View usage` below uses it), and this opens a
          // popover rather than navigating.
          className="min-h-[24px] min-w-[24px] shrink-0 px-1 text-xs font-normal tabular-nums underline decoration-dotted underline-offset-2"
          aria-label={`Show ${hiddenCount} hidden account${hiddenCount === 1 ? "" : "s"}`}
          data-testid="footer-account-overflow"
        >
          +{hiddenCount} account{hiddenCount === 1 ? "" : "s"}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="max-h-72 overflow-y-auto p-2" align="end">
        <AccountList accounts={fit.all} />
      </PopoverContent>
    </Popover>
  );
}
