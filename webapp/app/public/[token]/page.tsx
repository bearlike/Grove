import { PublicWorkspace } from "@/components/grove/public/public-workspace";

/** Route files name the segment; the public surface owns its client-side polling and rendering. */
export default function PublicWorkspacePage(): React.ReactNode {
  return <PublicWorkspace />;
}
