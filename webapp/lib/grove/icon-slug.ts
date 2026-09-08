/** `prefix:name`, the only shape Iconify resolves; anything else is a typo or an injection. */
const ICON_SLUG = /^[a-z0-9-]+:[a-z0-9-]+$/;

/** True when Iconify would accept `slug` as an icon name. */
export function isToolIconSlug(slug: unknown): slug is string {
  return typeof slug === "string" && ICON_SLUG.test(slug);
}
