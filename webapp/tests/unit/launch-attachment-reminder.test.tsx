import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";

import { LaunchAttachmentChips } from "@/components/grove/launch/composer-files";

it("describes missing restored bytes as a re-add reminder, not an upload failure", () => {
  const html = renderToStaticMarkup(
    <LaunchAttachmentChips
      files={[]}
      pendingReAdd={["brief.txt"]}
      onRemove={() => undefined}
      onReplace={() => undefined}
    />,
  );
  expect(html).toContain("Re-add file");
  expect(html).toContain("brief.txt");
  expect(html).not.toContain("Upload failed");
  expect(html).toContain('role="status"');
});
