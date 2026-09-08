import type { Metadata } from "next";

import { Gallery } from "@/components/grove/gallery/gallery";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { sectionFor } from "@/components/grove/shell/nav";

/** From `NAV_ITEMS`, like every other destination — see `app/(shell)/page.tsx`. */
export const metadata: Metadata = { title: sectionFor("/gallery").label };

/** Every .drawio on the host, attributed to its workspace and session. */
export default function GalleryPage(): React.ReactNode {
  return (
    <>
      <ShellHeader />
      <Gallery />
    </>
  );
}
