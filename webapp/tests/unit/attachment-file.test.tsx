import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AttachmentFile } from "@/components/grove/attachment-file";

/**
 * The ONE file row every surface draws.
 *
 * Three surfaces compose it — the landing composer, the workspace composer and
 * a sent turn — and the whole reason it is one module is that a file must not
 * draw one way staged and another way delivered. What is pinned is what a call
 * site depends on: that it IS the vendored `File` element at the size the
 * design asked for, that it ALWAYS states what it knows about the file, and
 * that a size nobody measured is named as unrecorded rather than invented.
 *
 * SSR-only by design (see `vitest.config.ts`): a static render sees the
 * vendored slots and the words, which is exactly what regresses. Geometry —
 * the card's gradient, its radius role, the filename's rendered size, two
 * contexts agreeing — is a browser fact and lives in
 * `tests/e2e/shared-composer.spec.ts`; the theme's source form is pinned in
 * `attachment-card-theme.test.ts`.
 */

/**
 * The markup of ONE slotted element, children included.
 *
 * Slice-to-end would be enough for "does this token appear after that one",
 * and that is precisely the assertion this file must not make: `file-size`
 * living INSIDE `file-metadata` is the contract, and a size slot sitting
 * beside the container would satisfy a naive search identically.
 */
function slot(html: string, name: string): string {
  const marker = html.indexOf(`data-slot="${name}"`);
  expect(marker, `no data-slot="${name}" in ${html}`).toBeGreaterThan(-1);
  const start = html.lastIndexOf("<", marker);
  const tag = /^[a-z0-9]+/i.exec(html.slice(start + 1))![0]!;
  const boundaries = new RegExp(`<${tag}\\b|</${tag}>`, "gi");
  boundaries.lastIndex = start;
  let depth = 0;
  for (let match = boundaries.exec(html); match; match = boundaries.exec(html)) {
    depth += match[0]!.startsWith("</") ? -1 : 1;
    if (depth === 0) return html.slice(start, match.index + match[0]!.length);
  }
  throw new Error(`unclosed <${tag}> around data-slot="${name}"`);
}

/** The words a reader actually sees in a fragment. */
function words(fragment: string): string {
  return fragment.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
}

describe("AttachmentFile", () => {
  it("is the vendored File element, as a named card, at the small size", () => {
    const html = renderToStaticMarkup(<AttachmentFile name="notes.txt" size={2048} />);
    expect(html).toContain('data-slot="file-root"');
    expect(html).toContain('data-size="sm"');
    // Outline is `File.Root`'s default variant; a caller here passes none, so
    // a different look means somebody restyled the composition.
    expect(html).not.toContain('data-variant="');
    // The theme hangs off this class, so the class IS the seam between the
    // component and `globals.css` — see `attachment-card-theme.test.ts`.
    expect(html).toContain("attachment-card");
    // A row is an object with a name and two lines, not a run of loose text:
    // without a role the name and the size are two unrelated strings to a
    // screen reader walking the message.
    expect(html).toContain('role="group"');
    expect(html).toContain('data-slot="file-icon"');
    expect(html).toContain('data-slot="file-name"');
  });

  it("always draws the metadata line, whatever it has to put in it", () => {
    // Three states, three different sentences, ONE container — a row whose
    // second line appears and disappears changes height as it changes meaning,
    // and the card's own measure depends on it being there.
    for (const html of [
      renderToStaticMarkup(<AttachmentFile name="notes.txt" size={2048} />),
      renderToStaticMarkup(<AttachmentFile name="notes.txt" />),
      renderToStaticMarkup(<AttachmentFile name="notes.txt" size={2048} state="uploading" />),
      renderToStaticMarkup(<AttachmentFile name="notes.txt" size={2048} state="error" />),
    ]) {
      expect(words(slot(html, "file-metadata"))).not.toHaveLength(0);
    }
  });

  it("states the file's kind beside a size it was actually given", () => {
    const cases = [
      ["Image", <AttachmentFile key="i" name="shot.png" contentType="image/png" size={2048} />],
      ["PDF", <AttachmentFile key="p" name="spec.pdf" size={2048} />],
      ["Video", <AttachmentFile key="v" name="clip.mp4" size={2048} />],
      ["File", <AttachmentFile key="f" name="core.dump" size={2048} />],
    ] as const;
    for (const [kind, element] of cases) {
      const metadata = slot(renderToStaticMarkup(element), "file-metadata");
      // The vendored formatter is the one that may print a byte count, so the
      // size slot has to be INSIDE the line that claims to describe the file.
      expect(metadata).toContain('data-slot="file-size"');
      expect(words(metadata), kind).toMatch(
        new RegExp(`${kind}\\b[^0-9]*2\\.0 KB|2\\.0 KB[^A-Za-z]*${kind}\\b`),
      );
    }
  });

  it("says a size was never recorded rather than fabricating or omitting one", () => {
    // The vendored formatter takes a bare number, so an absent one reaching it
    // renders the literal `NaN MB` — which carries no digits and reads as a
    // measurement. The row must not call it at all, and must still SAY so: a
    // blank second line is indistinguishable from a zero-byte file.
    for (const html of [
      renderToStaticMarkup(<AttachmentFile name="shot.png" contentType="image/png" />),
      renderToStaticMarkup(<AttachmentFile name="shot.png" contentType="image/png" size={null} />),
    ]) {
      const metadata = words(slot(html, "file-metadata"));
      expect(metadata).toContain("Size not recorded");
      // With the kind still stated, so an unweighed file says LESS than a
      // weighed one rather than something different.
      expect(metadata).toContain("Image");
      expect(html).not.toContain('data-slot="file-size"');
      expect(html).not.toContain("NaN");
    }
  });

  it("says an upload is running WITHOUT dropping what it already knows", () => {
    const html = renderToStaticMarkup(
      <AttachmentFile name="notes.txt" size={2048} state="uploading" onRemove={() => {}} />,
    );
    expect(html).toContain('data-state="uploading"');
    expect(words(html)).toContain("ploading");
    // The size is a FACT about the file, not a property of the upload: losing
    // it mid-flight makes the row twitch between two shapes on every send.
    expect(words(slot(html, "file-metadata"))).toContain("2.0 KB");
    expect(html).not.toContain("Remove notes.txt");
  });

  it("says a failed upload failed, in words, beside the size it still knows", () => {
    const html = renderToStaticMarkup(
      <AttachmentFile name="notes.txt" size={2048} state="error" onRemove={() => {}} />,
    );
    expect(html).toContain('data-state="error"');
    const metadata = words(slot(html, "file-metadata"));
    expect(metadata).toContain("Upload failed");
    expect(metadata).toContain("2.0 KB");
    expect(html).toContain("Remove notes.txt");
  });

  it("floors the remove control's hit area in PIXELS, the one honest literal", () => {
    // A pointer's physical size does not shrink when type gets denser, so the
    // floor must NOT track the density root — the vendored icon button is
    // `size-6`, 19.2px at this root (design-system §1).
    const html = renderToStaticMarkup(<AttachmentFile name="notes.txt" onRemove={() => {}} />);
    expect(html).toMatch(/min-h-\[24px\][^"]*min-w-\[24px\]|min-w-\[24px\][^"]*min-h-\[24px\]/);
  });

  it("draws no remove affordance at all when the caller supplies no verb", () => {
    // A sent turn is the case: the file is delivered, and a control that
    // cannot act is worse than an absent one.
    expect(renderToStaticMarkup(<AttachmentFile name="notes.txt" state="error" />)).not.toContain(
      "Remove notes.txt",
    );
    expect(renderToStaticMarkup(<AttachmentFile name="notes.txt" />)).not.toContain("Remove notes.txt");
  });

  it("keeps two same-named rows independently removable", () => {
    // The verb takes no argument, so the caller closes over the identity it
    // actually holds (an index, a runtime scope) rather than a filename two
    // staged files may legitimately share.
    const html = renderToStaticMarkup(
      <>
        <AttachmentFile name="notes.txt" onRemove={() => {}} />
        <AttachmentFile name="notes.txt" onRemove={() => {}} />
      </>,
    );
    expect(html.match(/Remove notes\.txt/g)).toHaveLength(2);
  });

  it("offers Annotate on an IMAGE that is done and has a caller to hand the edit to", () => {
    const html = renderToStaticMarkup(
      <AttachmentFile name="shot.png" size={2048} onEdit={() => {}} onRemove={() => {}} />,
    );
    expect(html).toContain("Annotate shot.png");
    // Both verbs floor their hit area the same way: a pointer target does not
    // shrink with the type ramp.
    expect(html.match(/min-h-\[24px\]/g)).toHaveLength(2);
  });

  it("never offers Annotate where it could not act", () => {
    // Not an image: the editor rasterizes pixels and a text file has none.
    expect(
      renderToStaticMarkup(<AttachmentFile name="notes.txt" onEdit={() => {}} />),
    ).not.toContain("Annotate");
    // Mid-upload: the bytes are leaving; editing them now would race the send.
    expect(
      renderToStaticMarkup(<AttachmentFile name="shot.png" state="uploading" onEdit={() => {}} />),
    ).not.toContain("Annotate");
    // A sent turn supplies no verb, so the delivered row carries no control.
    expect(renderToStaticMarkup(<AttachmentFile name="shot.png" />)).not.toContain("Annotate");
    // The browser's recorded type wins over the name, both ways round.
    expect(
      renderToStaticMarkup(
        <AttachmentFile name="blob" contentType="image/webp" onEdit={() => {}} />,
      ),
    ).toContain("Annotate blob");
  });

  it("carries the full name for a KEYBOARD reader, not only for a pointer", () => {
    // `title` is a hover affordance and reaches nobody who is not holding a
    // mouse; the truncated name is all a screen reader would otherwise get.
    // The row's own accessible name is what carries the rest.
    const name = "a-very-long-attachment-filename-that-will-be-clipped.txt";
    const html = renderToStaticMarkup(<AttachmentFile name={name} size={2048} />);
    expect(slot(html, "file-name")).toContain("truncate");
    expect(html).toContain('data-slot="tooltip-trigger"');
    expect(html).toContain('tabindex="0"');
    const label = /aria-label="([^"]*)"/.exec(slot(html, "file-root"))?.[1] ?? "";
    expect(label, "the row's accessible name omits the full filename").toContain(name);
  });
});

describe("every surface draws THIS row and nothing else", () => {
  // A source census, because the two runtime surfaces need a live assistant-ui
  // runtime to mount and the defect — a second file vocabulary creeping back in
  // on one of them — is invisible in a render that has no attachments in it.
  const SURFACES = [
    "components/grove/launch/composer-files.tsx",
    "components/grove/workspace/composer-attachment.tsx",
  ] as const;

  it.each(SURFACES)("%s composes AttachmentFile", (path) => {
    const text = readFileSync(path, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    expect(text).toContain('from "@/components/grove/attachment-file"');
    expect(text).toContain("<AttachmentFile");
    expect(text).not.toMatch(/ComposerAttachmentChip|ComposerFile\b|FileTextIcon|<File\.Root/);
  });

  it("the vendored chip has no caller left under components/grove", () => {
    const grove = ["components/grove/composer.tsx", ...SURFACES, "components/grove/workspace/thread.tsx"];
    for (const path of grove) {
      expect(readFileSync(path, "utf8"), path).not.toContain("ComposerAttachmentChip");
    }
  });

  it("only the composers pass a remove verb, and a sent turn passes none", () => {
    // The same row on three surfaces is the point; the ONE prop that may
    // differ is the verb, and a transcript that grew one would offer to delete
    // a file that has already been delivered.
    for (const path of SURFACES) {
      expect(readFileSync(path, "utf8"), path).toContain("onRemove");
    }
    const sent = readFileSync("components/grove/workspace/composer-attachment.tsx", "utf8");
    const messageRow = sent.slice(sent.indexOf("function MessageAttachmentRow"));
    expect(messageRow.slice(0, messageRow.indexOf("function ", 1))).not.toContain("onRemove");
  });
});
