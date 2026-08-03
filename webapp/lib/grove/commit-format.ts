/**
 * Parse a git commit subject into a muted conventional-commit tag + clean text.
 *
 * Grove's own commits lead with a gitmoji (`✨ feat(scope): …`, `🐛 fix: …`),
 * which renders inconsistently and clashes with the restrained, color-rationed
 * palette. Strip the leading gitmoji (unicode glyph or `:shortcode:`) and
 * surface the conventional-commit *type* as a small muted text tag (`feat` /
 * `fix` / `perf`) instead — defined once here so the card footer and the
 * commit list can't drift.
 */

export interface CommitParts {
  /** Conventional-commit type (`feat`, `fix`, `perf`, …) lower-cased, or null. */
  tag: string | null;
  /** The subject with the leading gitmoji + `type(scope):` prefix removed. */
  subject: string;
}

// A leading gitmoji: a pictographic glyph (optional VS16) OR a `:shortcode:`,
// plus trailing whitespace. The `u` flag is required for \p{...} classes.
const GITMOJI = /^(?:\p{Extended_Pictographic}️?|:[a-z0-9_+-]+:)\s*/u;
// Conventional-commit prefix: `type` or `type(scope)` with an optional `!`.
const CONVENTIONAL = /^([a-z]+)(?:\([^)]*\))?!?:\s*/i;

export function parseCommitSubject(raw: string): CommitParts {
  const stripped = raw.replace(GITMOJI, "").trimStart();
  const match = CONVENTIONAL.exec(stripped);
  if (match) {
    return { tag: match[1].toLowerCase(), subject: stripped.slice(match[0].length) };
  }
  return { tag: null, subject: stripped };
}
