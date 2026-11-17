/**
 * Workspace artifacts as pure functions of their wire rows: the name a row
 * shows, the line under it, which icon source applies and which viewer opens.
 */

import type { ArtifactRef } from "@/lib/grove/api";

/** The last path segment of a file target or a URL's path, without a query. */
function lastSegment(target: string): string {
  const path = target.split(/[?#]/)[0] ?? target;
  return path.replace(/\/+$/, "").split("/").pop() ?? "";
}

/**
 * The row's title: the agent's own label when it gave one, else the file's
 * name, else the link's host. A link's last path segment is usually an opaque
 * id, so the host is the more recognisable fallback.
 */
export function artifactName(ref: ArtifactRef): string {
  if (ref.title) return ref.title;
  if (ref.kind === "file") return lastSegment(ref.target) || ref.target;
  return artifactHost(ref.target) ?? ref.target;
}

/**
 * The line under the title: where the artifact is.
 *
 * A file inside the workspace checkout or the project reads as a relative
 * path, which is how the agent named it and keeps the host's directory layout
 * off the page. A file elsewhere (an allowed host directory such as `/tmp`)
 * has no shorter honest name, so it shows as attached.
 *
 * @param roots The workspace checkout and the project root, most specific first.
 */
export function artifactDetail(ref: ArtifactRef, roots: readonly string[]): string {
  if (ref.kind === "link") return ref.target;
  for (const root of roots) {
    const prefix = root.endsWith("/") ? root : `${root}/`;
    if (ref.target.startsWith(prefix)) return ref.target.slice(prefix.length);
  }
  return ref.target;
}

function artifactHost(target: string): string | null {
  try {
    return new URL(target).host || null;
  } catch {
    return null;
  }
}

/**
 * The site's own `/favicon.ico`, or `null` for anything that is not an
 * http(s) link.
 *
 * Asked of the site itself rather than a favicon service: a third-party
 * lookup would tell that service every document host a workspace links to.
 * A site with no icon at that path fails the image load, and the row falls
 * back to a generic link mark.
 */
export function artifactFaviconUrl(ref: ArtifactRef): string | null {
  if (ref.kind !== "link") return null;
  try {
    const url = new URL(ref.target);
    return /^https?:$/.test(url.protocol) ? `${url.origin}/favicon.ico` : null;
  } catch {
    return null;
  }
}

/** A `.drawio` file opens in the read-only diagram viewer instead of the file viewer. */
export function artifactIsDiagram(ref: ArtifactRef): boolean {
  return ref.kind === "file" && ref.target.toLowerCase().endsWith(".drawio");
}

/** Extensions the file viewer has no renderer for, and the one that renders them properly. */
const VIEWER_ALIASES: ReadonlyMap<string, string> = new Map([
  // The viewer colours added and removed lines for `.patch` only.
  ["diff", "patch"],
  ["jsonl", "json"],
  ["ndjson", "json"],
]);

/** A Jinja suffix says "template", not what the file is: `nginx.conf.j2`, `plan.md.j2`. */
const TEMPLATE_SUFFIX = /\.(j2|jinja2?)$/i;

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/**
 * The format to render a file as, when its own extension would be refused.
 *
 * The viewer picks a renderer by extension and rejects one it does not know,
 * so a diff, a JSON Lines log and a Jinja template are rendered as the nearest
 * format it does know. A template is read as the file it produces
 * (`plan.md.j2` as Markdown), or as plain text when only the suffix says what
 * it is.
 *
 * @param name The artifact's file name.
 * @returns The extension to render as, or `undefined` to let the name decide.
 */
export function artifactViewerType(name: string): string | undefined {
  if (TEMPLATE_SUFFIX.test(name)) {
    const inner = name.replace(TEMPLATE_SUFFIX, "");
    return artifactViewerType(inner) ?? (extension(inner) || "txt");
  }
  return VIEWER_ALIASES.get(extension(name));
}
