import { redirect } from "next/navigation";

/**
 * `/activity` folded into `/` (issue #89) — the home grid and the activity wall
 * are now ONE unified workspace surface. The route is kept (not deleted) so
 * existing bookmarks/links don't 404; it just redirects to the merged surface.
 */
export default function ActivityPage() {
  redirect("/");
}
