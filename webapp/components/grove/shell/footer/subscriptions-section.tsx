"use client";

import { CircleAlertIcon } from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { QUOTA_TEXT_TONE } from "@/components/grove/shell/footer/quota-tone";
import { Glyph, Groups, Section, Value } from "@/components/grove/shell/footer/primitives";
import { AccountOverflow } from "@/components/grove/shell/status-footer-accounts";
import { useMinWidth } from "@/components/grove/workspace/use-min-width";
import {
  accountTierLabel,
  fitAccounts,
  MAX_INLINE_ACCOUNTS,
  percentLabel,
  quotaTone,
  quotaUrgent,
  type AccountSummary,
} from "@/lib/grove/adapters/footer";


/**
 * ONE ACCOUNT IS ONE GROUP, ANCHORED BY ITS BRAND MARK.
 *
 * The reported failure was not being able to tell which `5h` or `7d` belonged
 * to which account. The first repair drew more lines and made it worse: the
 * plan and its reading were seamed apart and every account carried a border,
 * so a two-account section held five verticals and no anchor. THE ANCHOR IS
 * THE FIX. Each group LEADS with the provider's mark — the identity every
 * fleet row already leads with — then states the tier and the reading as one
 * quiet run with whitespace between them. A reader finds the logo, and
 * everything to its right until the next logo is that account.
 *
 * The provider word is dropped from the text (`max 20x`, not `Claude max
 * 20x`) because the mark already says it; the full identity is one hover
 * away. Tone moves ONLY on the percentage, the one value in the band that
 * changes with headroom, so a section of healthy accounts is one colour and
 * an exhausted one is found by its single red figure.
 */
function Account({ account }: { account: AccountSummary }): React.ReactNode {
  const reading = account.percent === null ? "not measured" : `${percentLabel(account.percent)}%`;
  const tone = quotaTone(account.percent);
  return (
    <span
      className="inline-flex min-w-0 items-center gap-1.5"
      title={`${account.label} · ${account.window ?? "window"} ${reading}`}
      data-testid="footer-account"
    >
      <AgentMark agentName={account.provider} className="size-[1em] shrink-0" />
      {/* `label`, the tighter ceiling: a plan is CONFIRMED, not recognised —
          you already know which subscriptions you have, and the mark beside it
          has already said which provider. Unbounded, `generic
          token-plan-individual` took 133px of the band, more than the project
          and the branch together. */}
      <Value title={account.label}>{accountTierLabel(account)}</Value>
      <span className="inline-flex shrink-0 items-center gap-1 tabular-nums">
        <span className="text-content-tertiary">{account.window ?? "window"}</span>
        {account.percent === null ? (
          <span className="text-content-tertiary">—</span>
        ) : (
          // THE PERCENTAGE ALONE takes the ramp and the pulse. Toning the plan
          // beside it would make a whole row shout, and this band's job is to
          // stay still; the reading is the part that changes.
          <span
            className={`${QUOTA_TEXT_TONE[tone!]} ${quotaUrgent(account.percent) ? "quota-urgent" : ""}`}
            data-testid="footer-account-percent"
            data-tone={tone}
          >
            {percentLabel(account.percent)}%
          </span>
        )}
        {account.stale ? <span className="text-warning">stale</span> : null}
      </span>
    </span>
  );
}

/**
 * The width at which the strip has room for a SECOND account.
 *
 * Below it the section shows one — the fullest, since `accountSummaries` ranks
 * by headroom — and the rest move into the overflow control that already
 * exists. Measured on the deployed band: at 1024px the two accounts plus their
 * readings claimed 280px of a 1024px row, which is what squeezed `main` to 11px
 * of the 24 it needed. Dropping the second returns ~70px to the values that
 * identify where you are.
 *
 * `xl` (1280px) rather than a new number: it is the next step up from the `lg`
 * the whole strip already switches on, and the band's own breakpoints should be
 * few and shared. A capacity is not a second layout — the same composition
 * shows fewer values, which is the rule the narrow band already follows.
 */
const TWO_ACCOUNT_WIDTH = 1280;

export function SubscriptionsSection({
  accounts,
  connected,
}: {
  accounts: AccountSummary[];
  connected: boolean;
}): React.ReactNode {
  // `fitAccounts` has always taken a capacity and nothing had ever passed one,
  // so the ceiling was also the floor: two accounts at every width down to the
  // `lg` cutover. This is the caller that makes the parameter real.
  const fit = fitAccounts(accounts, useMinWidth(TWO_ACCOUNT_WIDTH) ? MAX_INLINE_ACCOUNTS : 1);
  return (
    <Section id="footer-subscriptions" wash className="shrink min-w-0">
      {connected ? (
        // Accounts are GROUPS, so the section's own seam divides them — one
        // rule between two accounts, none inside either.
        <Groups>
          {fit.inline.map((account) => <Account key={account.accountId} account={account} />)}
          {/* `shrink-0` declares this group RIGID to `Groups`, which mirrors it
              onto the wrapper. The control's own button is already `shrink-0`
              inside `AccountOverflow`, but a component's internals are not
              visible to the element `Groups` inspects — so without this the
              wrapper took `min-w-0`, shrank past a button that refuses to
              shrink, and the section's clip cut `+3 accounts` by 7.5px at
              1024px. A group states its own rigidity at the point it is
              composed, never by having a parent guess at its internals. */}
          {fit.hidden.length > 0 ? (
            <span className="inline-flex shrink-0 items-center">
              <AccountOverflow fit={fit} />
            </span>
          ) : null}
        </Groups>
      ) : (
        <span className="inline-flex items-center gap-1 px-1 text-warning">
          <Glyph Icon={CircleAlertIcon} />
          quota not measured
        </span>
      )}
    </Section>
  );
}
