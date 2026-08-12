import type { TokenClassesView } from "@/lib/grove/api";
import { NOT_MEASURED } from "@/components/grove/duration";

// `NOT_MEASURED` and `duration` moved to `@/components/grove/duration` so a
// surface outside the usage tree (the session table, the session detail
// header) reads the same vocabulary instead of growing a second formatter
// that could drift from this one. Re-exported here so every existing import
// of `./format` keeps working unchanged.
export { NOT_MEASURED, duration } from "@/components/grove/duration";

/**
 * The classes that ADD UP to a total. `provider_total` is deliberately absent:
 * it is the provider's own sum over these same classes, so including it in the
 * reduce double-counts every session the provider reported (a Codex session
 * with 233M cache-read tokens read as 331M).
 */
const TOKEN_CLASSES = [
  "fresh_input",
  "cache_read",
  "cache_creation",
  "reasoning",
  "output",
] as const satisfies readonly (keyof TokenClassesView)[];

/** The measured token total, or `null` when nothing in the class set was reported. */
export function tokenTotal(tokens: TokenClassesView | undefined | null): number | null {
  const parts = TOKEN_CLASSES.map((key) => tokens?.[key]).filter(
    (value): value is number => typeof value === "number",
  );
  if (parts.length > 0) return parts.reduce((total, value) => total + value, 0);
  // A provider that reports only its own total still measured something.
  return typeof tokens?.provider_total === "number" ? tokens.provider_total : null;
}

export function tokenLabel(tokens: TokenClassesView | undefined | null): string {
  return `${abbreviate(tokenTotal(tokens))} tokens`;
}

export function money(amount: string, currency: string): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(
    Number(amount),
  );
}

const UNITS = ["", "K", "M", "B", "T", "Q"] as const;

/**
 * The shortest honest rendering of a count: `204643` → `204.6K`.
 *
 * Always one decimal above a thousand, so a column of abbreviations has one
 * shape and `1.0K` never reads as a different precision from `1.2K`. The exact
 * value is not lost — `<AbbreviatedNumber>` pairs this with a tooltip carrying
 * `exact()`.
 *
 * `en-US` rather than the ambient locale, matching the vendored `NumberTicker`:
 * grouping that changes with the host's `LANG` is untestable and makes two
 * numbers on the same screen disagree.
 */
export function abbreviate(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return NOT_MEASURED;

  const sign = value < 0 ? "-" : "";
  let scaled = Math.abs(value);
  let unit = 0;
  while (scaled >= 1000 && unit < UNITS.length - 1) {
    scaled /= 1000;
    unit += 1;
  }
  // Rounding can push the mantissa back over the threshold (999_999 → 1000.0K).
  // Step up once more so an abbreviation never carries four leading digits.
  if (unit < UNITS.length - 1 && Number(scaled.toFixed(1)) >= 1000) {
    scaled /= 1000;
    unit += 1;
  }

  return unit === 0
    ? `${sign}${scaled.toLocaleString("en-US", { maximumFractionDigits: 1 })}`
    : `${sign}${scaled.toFixed(1)}${UNITS[unit]}`;
}

/** The full number, for the tooltip behind an abbreviation. */
export function exact(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return NOT_MEASURED;
  return value.toLocaleString("en-US");
}

/** A percentage as the providers report it — whole numbers, one decimal at most. */
export function percent(value: number): string {
  return `${Number(value.toFixed(1)).toLocaleString("en-US")}%`;
}

/**
 * An absolute local timestamp. Absolute, not relative ("in 4 days"), because
 * these are quota resets and refresh times a reader compares against a clock.
 */
export function timestamp(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" }) : NOT_MEASURED;
}

/** The tail of a project path — the whole path is noise in a narrow column. */
export function projectLabel(path: string | null | undefined): string {
  if (!path) return "unknown project";
  const parts = path.split("/").filter(Boolean);
  return parts.slice(-2).join("/") || path;
}

/** `weekly_all` → `Weekly all`. The daemon's labels are wire identifiers. */
export function humanize(token: string): string {
  const spaced = token.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
