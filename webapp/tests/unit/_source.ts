import { readFileSync } from "node:fs";

/**
 * A source file with its comment bodies blanked, for any census asserting that
 * a token is ABSENT.
 *
 * WHY THIS EXISTS, and it is the same reason `scripts/lint-styling.ts` blanks
 * comments before scanning: the developer most likely to write `(i)`,
 * `max(12px` or `classNames` is the one explaining why the code no longer uses
 * it. Scanning raw text therefore flags exactly the file that fixed the defect,
 * and the failure reads as a regression. Three separate assertions hit this
 * within one session before it was worth extracting.
 *
 * Deliberately naive — no string- or regex-literal awareness. A census asks
 * "does this token appear in code", and a token inside a string literal is a
 * true match for that question.
 */
export function code(path: string): string {
  return readFileSync(path, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/[^\n]*/g, "");
}
