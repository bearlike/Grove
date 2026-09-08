import bundle from "./tool-icon-bundle.json";

const sets: Record<string, { icons: Record<string, { body: string }> }> = bundle;
const tints = new Map<string, string | null>();

/** Pick an accent from bundled artwork, not a dominant-colour or contrast estimate. */
export function iconTint(slug: string | null | undefined): string | null {
  if (!slug) return null;
  if (tints.has(slug)) return tints.get(slug)!;
  const [prefix, name] = slug.split(":");
  const body = sets[prefix]?.icons[name]?.body ?? "";
  let tint: string | null = null;
  for (const match of body.matchAll(/(?:fill|stroke|stop-color)="(#(?:[\da-f]{6}|[\da-f]{3}))"/gi)) {
    const raw = match[1].slice(1);
    const hex = raw.length === 3 ? [...raw].map(c => c + c).join("") : raw;
    const rgb = [0, 2, 4].map(offset => parseInt(hex.slice(offset, offset + 2), 16));
    // Ignore neutral outlines and highlights; unknown/monochrome marks stay neutral.
    if (Math.max(...rgb) - Math.min(...rgb) < 24) continue;
    tint = `#${hex.toLowerCase()}`;
    break;
  }
  tints.set(slug, tint);
  return tint;
}
