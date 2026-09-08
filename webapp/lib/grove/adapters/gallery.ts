/**
 * The diagram gallery, as pure functions of its wire rows.
 *
 * Narrowing, ordering, the card's two lines, which verb the menu's last item
 * is, and the two draw.io protocol frames the gallery adds to `drawio.ts`
 * (a chromeless viewer mount and a high-resolution export). No `window`, no
 * clock read of its own — everything a browser would supply is an argument.
 */

import type { GalleryItemView } from "@/lib/grove/api";

export type GallerySort = "newest" | "name" | "project";

export interface GalleryFilter {
  readonly query: string;
  /** A `repo_root`, or `null` for every project. */
  readonly project: string | null;
  readonly sort: GallerySort;
}

export const NO_GALLERY_FILTER: GalleryFilter = { query: "", project: null, sort: "newest" };

/**
 * The card's title: the SESSION's title when Grove has one, else the
 * workspace's, else the session's short id — and only with nothing else to
 * say, the file's own name.
 *
 * The reference the page was drawn against titles a card by the thing that
 * produced the file, not the file. A session inherits its workspace's title
 * on the wire (`session_title` IS `workspace_title` for a Grove-launched
 * session), so the first two arms usually agree; the order matters for a
 * hand-started session in a managed worktree, whose row names the workspace
 * but no session.
 */
export function galleryCardTitle(item: GalleryItemView): string {
  if (item.session_title) return item.session_title;
  if (item.workspace_title) return item.workspace_title;
  if (item.session_id) return item.session_id.slice(0, 8);
  return item.name;
}

/** `N pages • Project`, the card's second line. A page count nobody parsed is left off. */
export function galleryCardCaption(item: GalleryItemView): string {
  const pages =
    item.pages === null ? null : `${item.pages} ${item.pages === 1 ? "page" : "pages"}`;
  return [pages, item.repo_name].filter((part): part is string => part !== null).join("  •  ");
}

/**
 * Which "open" verb the card menu ends with.
 *
 * `workspace` when the owning workspace is live — attach there, the agent is
 * still in it. `session` when a transcript exists but nothing is running: the
 * session catalog's drill-in is what reads it. `null` when the file belongs
 * to neither, which is an ordinary answer for a tracked diagram in a repo
 * nobody is working in right now.
 */
export function galleryOpenTarget(
  item: GalleryItemView,
): { kind: "workspace"; href: string } | { kind: "session"; href: string } | null {
  if (item.workspace_id && item.workspace_live) {
    return { kind: "workspace", href: `/w/${encodeURIComponent(item.workspace_id)}` };
  }
  if (item.session_id && item.session_kind && item.session_cwd) {
    const query = new URLSearchParams({ kind: item.session_kind, cwd: item.session_cwd });
    return {
      kind: "session",
      href: `/sessions/${encodeURIComponent(item.session_id)}?${query.toString()}`,
    };
  }
  if (item.workspace_id) {
    return { kind: "workspace", href: `/w/${encodeURIComponent(item.workspace_id)}` };
  }
  return null;
}

/** Every distinct project on the listing, for the filter, alphabetised by name. */
export function galleryProjects(
  items: readonly GalleryItemView[],
): readonly { root: string; name: string }[] {
  const seen = new Map<string, string>();
  for (const item of items) seen.set(item.repo_root, item.repo_name);
  return [...seen.entries()]
    .map(([root, name]) => ({ root, name }))
    .sort((a, b) => a.name.localeCompare(b.name) || a.root.localeCompare(b.root));
}

/**
 * Narrow, then order. The query matches what the card SHOWS — title, caption,
 * file name, relative path, workspace, branch and session id — because a
 * search that matches hidden fields makes rows appear for no visible reason.
 */
export function filterGallery(
  items: readonly GalleryItemView[],
  filter: GalleryFilter,
): readonly GalleryItemView[] {
  const needle = filter.query.trim().toLowerCase();
  const kept = items.filter((item) => {
    if (filter.project !== null && item.repo_root !== filter.project) return false;
    if (needle === "") return true;
    const haystack = [
      galleryCardTitle(item),
      item.name,
      item.relative_path,
      item.repo_name,
      item.workspace_title ?? "",
      item.workspace_branch ?? "",
      item.session_id ?? "",
    ]
      .join("\n")
      .toLowerCase();
    return haystack.includes(needle);
  });
  const sorted = [...kept];
  switch (filter.sort) {
    case "name":
      sorted.sort((a, b) => galleryCardTitle(a).localeCompare(galleryCardTitle(b)) || byNewest(a, b));
      break;
    case "project":
      sorted.sort((a, b) => a.repo_name.localeCompare(b.repo_name) || byNewest(a, b));
      break;
    default:
      sorted.sort(byNewest);
  }
  return sorted;
}

function byNewest(a: GalleryItemView, b: GalleryItemView): number {
  return b.modified_at.localeCompare(a.modified_at);
}

/** The count line under the grid, stated for BOTH bounds like the sessions page. */
export function galleryCountLabel(shown: number, total: number): string {
  const noun = total === 1 ? "diagram" : "diagrams";
  return shown === total ? `${total} ${noun}` : `${shown} of ${total} ${noun}`;
}

/**
 * The chromeless read-only viewer — `chrome=0` is draw.io's own read-only
 * mode. It draws no page tabs in embed mode, so the lightbox draws its own
 * (see `galleryPageDocument`). Query
 * parameters are owned here for the same reason `drawioEmbedUrl` owns its
 * own: a base that could contribute its own could switch the protocol off.
 */
const VIEWER_QUERY = ["embed=1", "proto=json", "configure=1", "spin=1", "chrome=0", "nav=1"];

export function galleryViewerUrl(base: string): string {
  return `${base}${base.includes("?") ? "&" : "?"}${VIEWER_QUERY.join("&")}`;
}

/**
 * A PNG export at a chosen scale. The `groveExport` token rides the request
 * so the reply can be correlated the way `previewToken` correlates a preview:
 * draw.io echoes the whole request back as the export event's `message`.
 *
 * NO `xml`, for `previewMessage`'s reason — an `xml` on an export request is
 * a document reload, not a render hint.
 */
export function galleryExportMessage(
  token: string,
  options: { pageId?: string; scale: number; transparent?: boolean },
): string {
  return JSON.stringify({
    action: "export",
    format: "png",
    scale: options.scale,
    border: 16,
    transparent: options.transparent ?? false,
    ...(options.pageId === undefined ? {} : { pageId: options.pageId }),
    groveExport: token,
  });
}

/** The export token echoed on a PNG reply, or `null` for a reply we did not ask for. */
export function galleryExportToken(message: unknown): string | null {
  if (typeof message !== "object" || message === null) return null;
  const token = (message as { groveExport?: unknown }).groveExport;
  return typeof token === "string" ? token : null;
}

/** `<name>.png` beside the diagram's own basename; the page name joins when there are several. */
export function galleryExportFilename(name: string, pageName?: string): string {
  const stem = name.replace(/\.drawio$/i, "") || "diagram";
  const page = pageName?.trim().replace(/[^\w.-]+/g, "-");
  return page ? `${stem}-${page}.png` : `${stem}.png`;
}

/** The pages of an `<mxfile>`, in document order — id and name off each `<diagram>` start tag. */
export function galleryPages(xml: string): readonly { id: string; name: string }[] {
  const pages: { id: string; name: string }[] = [];
  const tag = /<diagram\b([^>]*)>/gi;
  let match: RegExpExecArray | null;
  while ((match = tag.exec(xml)) !== null) {
    const attrs = match[1] ?? "";
    const id = /\bid=(?:"([^"]*)"|'([^']*)')/i.exec(attrs);
    const name = /\bname=(?:"([^"]*)"|'([^']*)')/i.exec(attrs);
    pages.push({
      id: id?.[1] ?? id?.[2] ?? String(pages.length),
      name: name?.[1] ?? name?.[2] ?? `Page ${pages.length + 1}`,
    });
  }
  return pages;
}

/**
 * The document reduced to ONE of its pages.
 *
 * The embed protocol's `load` has no page selector and the chromeless viewer
 * draws no page tabs (measured: `chrome=0` in embed mode mounts only the
 * diagram container), so the lightbox owns the tabs and switches pages by
 * handing the frame a document holding just the page asked for. `null` when
 * the page is not in the file, so a stale tab can never load an empty sheet.
 */
export function galleryPageDocument(xml: string, pageId: string): string | null {
  const open = /<mxfile\b[^>]*>/i.exec(xml);
  if (!open) return null;
  const block = /<diagram\b([^>]*)>[\s\S]*?<\/diagram>|<diagram\b[^>]*\/>/gi;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = block.exec(xml)) !== null) {
    const attrs = match[1] ?? "";
    const id = /\bid=(?:"([^"]*)"|'([^']*)')/i.exec(attrs);
    const found = id?.[1] ?? id?.[2] ?? String(index);
    if (found === pageId) return `${open[0]}${match[0]}</mxfile>`;
    index += 1;
  }
  return null;
}
