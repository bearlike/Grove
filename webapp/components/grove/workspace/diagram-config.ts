import { DEFAULT_DRAWIO_URL, sanitizeDrawioBase } from "@/lib/grove/adapters";

/**
 * Which draw.io host this deployment embeds.
 *
 * `NEXT_PUBLIC_GROVE_DRAWIO_URL` is read here as a LITERAL member expression
 * and not through a computed lookup, because Next inlines `NEXT_PUBLIC_*` at
 * build time by textual substitution — `process.env[name]` is not replaced and
 * reads `undefined` in the browser, which would present as "the operator's
 * setting is being ignored" with nothing to point at.
 *
 * The default is the official hosted editor, which means the diagram XML is
 * sent to a third party's JavaScript running in this iframe. That is a real
 * privacy property, not a footnote: an operator who cannot accept it points
 * this variable at their own draw.io deployment, and everything else works
 * unchanged.
 *
 * Returns `null` when a configured value fails `sanitizeDrawioBase` — the tab
 * then says so rather than quietly falling back to the public host, which would
 * be the opposite of what that operator asked for.
 */
export function resolvedDrawioBase(): string | null {
  const configured = process.env.NEXT_PUBLIC_GROVE_DRAWIO_URL;
  return configured === undefined || configured.trim() === ""
    ? DEFAULT_DRAWIO_URL
    : sanitizeDrawioBase(configured);
}
