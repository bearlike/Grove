import type { WhoamiView } from "@/lib/grove/api";

/**
 * What the version's tooltip says about currency — THREE distinct claims, and
 * the distinction is the whole point.
 *
 * "up to date" is a positive assertion that requires a successful check. When
 * `latest_version` is null the check has not succeeded — offline, first call,
 * or an error — and the only honest thing to say is that we do not know.
 * Collapsing the unknown case into the good case is how a user misses a
 * security release and believes they were told otherwise.
 *
 * The rail no longer renders any of this (#814): version and uptime MOVED to
 * the status footer rather than being duplicated there, because two copies of
 * one fact on one screen is how the two come to disagree. This rule outlived
 * the components that used to carry it, so it stays here as a pure helper the
 * footer reads — it is a claim about release currency, not about a rail.
 */
export function updateTitle(identity: WhoamiView): string {
  if (identity.update_available && identity.latest_version) {
    return `Grove ${identity.version} — ${identity.latest_version} is available`;
  }
  if (identity.latest_version) return `Grove ${identity.version} — up to date`;
  return `Grove ${identity.version} — no update information`;
}
