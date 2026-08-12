import { FileIcon, defaultStyles, type DefaultExtensionType } from "react-file-icon";

/**
 * A file's type as a COLOURED icon, from `react-file-icon`'s own palette.
 *
 * WHY a package and not our own set: the vendored `DiffViewer` draws a
 * monochrome text chip ("TS" in a grey square) and neither registry ships
 * anything better — assistant-ui's `file`, `elements-file-tree` and
 * `diff-viewer` all reach for lucide's flat `FileIcon`. Hand-drawing a couple
 * of hundred glyphs and picking their colours is the one thing this app exists
 * to avoid, so the icon set is a dependency and the colours are ITS data. No
 * hex literal appears here, and no Tailwind colour utility does either — which
 * is also what keeps `lint:styling` green in `components/grove`.
 *
 * The library's palette covers ~150 extensions but predates several suffixes an
 * agent edits constantly. {@link SAME_LANGUAGE} aliases only those, and only
 * where the alias is a fact rather than a taste — `.tsx` IS TypeScript. An
 * extension it still does not know renders as the library's neutral page with
 * its real extension on the label, never as a lookalike of some other language.
 */

/** Suffixes the palette lacks that are the SAME language as one it has. The
 * label still shows the real extension; only the colour and glyph are borrowed. */
const SAME_LANGUAGE: Record<string, DefaultExtensionType> = {
  tsx: "ts",
  mts: "ts",
  cts: "ts",
  mjs: "js",
  cjs: "js",
  yaml: "yml",
  mdx: "md",
  markdown: "md",
  htm: "html",
  cxx: "cpp",
  cc: "cpp",
};

/** The trailing extension, lowercased — `""` for a name that has none
 * (`Makefile`, `Dockerfile`) or is purely a dotfile (`.gitignore`). */
export function extensionOf(path: string): string {
  const name = path.slice(path.lastIndexOf("/") + 1);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** The library's style for an extension, resolving the same-language aliases.
 * Empty when it knows neither — the caller then gets an uncoloured page. */
export function fileIconStyle(extension: string): Partial<Parameters<typeof FileIcon>[0]> {
  const known = defaultStyles[extension as DefaultExtensionType];
  if (known) return known;
  const alias = SAME_LANGUAGE[extension];
  return alias ? defaultStyles[alias] : {};
}

export function FileTypeIcon({ path }: { path: string }) {
  const extension = extensionOf(path);
  return (
    // The SVG is `width: 100%` on a 40×48 viewBox, so the wrapper sets the
    // width and the height follows — a fixed `size-*` would squash it.
    <span aria-hidden className="w-3.5 shrink-0" data-testid="file-type-icon">
      <FileIcon extension={extension} {...fileIconStyle(extension)} />
    </span>
  );
}
