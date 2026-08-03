import { redirect } from "next/navigation";

/**
 * The home grid and the activity wall are ONE unified workspace surface at
 * `/`. This route is kept (not deleted) so existing bookmarks/links don't
 * 404; it just redirects to the merged surface.
 */
export default function ActivityPage() {
  redirect("/");
}
