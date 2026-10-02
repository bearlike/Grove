import { DEFAULT_FILE, getIconForFile } from "vscode-icons-js";

import { AppIcon } from "@/components/grove/app-icon";

/**
 * A file's type as its coloured VS Code icon — the artwork every editor user
 * already reads at a glance.
 *
 * Both halves are off the shelf. `vscode-icons-js` is the VS Code extension's
 * own filename → icon table (extensions, exact names like `package.json`,
 * dotfiles), and Iconify's `vscode-icons` set is that extension's artwork under
 * the same names, which `AppIcon` already renders. So this file is a name
 * translation and nothing else: no palette, no glyphs, no extension list kept
 * here to drift from the editor's.
 *
 * A name the table does not know gets the set's own generic page, never a
 * lookalike of some other language.
 */
export function fileIconSlug(path: string): string {
  const name = path.slice(path.lastIndexOf("/") + 1);
  // `file_type_reactts.svg` → `vscode-icons:file-type-reactts`. The package
  // answers every name (its generic page included) and types `undefined` only
  // nominally; the fallback narrows that to its own default.
  const file = getIconForFile(name) ?? DEFAULT_FILE;
  return `vscode-icons:${file.replace(/\.svg$/, "").replaceAll("_", "-")}`;
}

export function FileTypeIcon({ path }: { path: string }) {
  return <AppIcon slug={fileIconSlug(path)} className="size-4 shrink-0" data-testid="file-type-icon" />;
}
