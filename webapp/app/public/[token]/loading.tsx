import { PublicWorkspaceSkeleton } from "@/components/grove/public/public-workspace";

/** The segment wait keeps the public rail and reading surface visible from the first paint. */
export default function Loading(): React.ReactNode {
  return <PublicWorkspaceSkeleton />;
}
