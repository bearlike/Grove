import { describe, expect, it } from "vitest";

import {
  attachmentCountError,
  buildCreateRequest,
  MAX_ATTACHMENT_BYTES,
  MAX_ATTACHMENTS,
  type StagedAttachment,
} from "@/lib/grove/adapters";
import type { LaunchState, LaunchValues } from "@/components/grove/launch/launch-state";

/**
 * Files staged on the LANDING composer, which has no workspace to upload to.
 *
 * The workspace composer's attachments are covered by `attachments.test.ts`;
 * this pins the other delivery, where the bytes ride the create request itself
 * and `buildCreateRequest` is the only thing standing between a picker and the
 * daemon.
 */

const values: LaunchValues = {
  repoRoot: "/repos/grove",
  projectCwd: null,
  selectedProjectCwd: "/repos/grove",
  agentName: "claude",
  model: null,
  customModel: false,
  runtime: null,
  brief: null,
  native: null,
  branchMode: "auto",
  branchName: "",
  existingBranch: "",
  remoteRef: "",
  localName: "",
  baseRef: "main",
  skipInit: false,
  titleOverride: null,
  ticket: null,
};

const state: LaunchState = { values, touched: new Set() };

function staged(name: string, size = 12): StagedAttachment {
  return { name, size, content_base64: "Zm9v" };
}

describe("attachmentCountError", () => {
  it("permits exactly the wire cap and refuses one past it", () => {
    expect(attachmentCountError(MAX_ATTACHMENTS)).toBeNull();
    expect(attachmentCountError(MAX_ATTACHMENTS + 1)).toContain(String(MAX_ATTACHMENTS));
  });
});

describe("buildCreateRequest with attachments", () => {
  it("omits the field entirely when nothing is staged", () => {
    // Not `attachments: []`. The engine defaults the field, and a request that
    // grew a key would be a different request for every create that attaches
    // nothing — which is nearly all of them.
    expect(buildCreateRequest(state, "Build it")).not.toHaveProperty("attachments");
    expect(buildCreateRequest(state, "Build it", [])).not.toHaveProperty("attachments");
  });

  it("is byte-identical to today's request when nothing is staged", () => {
    // The acceptance criterion for the whole change: adding a third parameter
    // must not move a single byte on the path everyone already uses. Compared
    // as JSON because key ORDER is part of "identical" here.
    expect(JSON.stringify(buildCreateRequest(state, "Build it", []))).toBe(
      JSON.stringify(buildCreateRequest(state, "Build it")),
    );
  });

  it("carries staged files in pick order", () => {
    const request = buildCreateRequest(state, "Read these", [
      staged("spec.md"),
      staged("trace.log"),
      staged("shot.png"),
    ]);

    // Order is the contract: the engine writes the files and lists them in the
    // fenced block in the order it received them, so the transcript reads back
    // in the order the picker showed.
    expect(request.attachments).toEqual([
      { name: "spec.md", content_base64: "Zm9v" },
      { name: "trace.log", content_base64: "Zm9v" },
      { name: "shot.png", content_base64: "Zm9v" },
    ]);
  });

  it("drops `size`, which is the composer's own bookkeeping and not on the wire", () => {
    // `attachments` is OPTIONAL on the wire, so it is read as such here rather
    // than with a non-null assertion: the field being absent is the ordinary
    // case (see the omission test above), and a `!` would hide a build that
    // silently stopped sending it.
    const sent = buildCreateRequest(state, "Read it", [staged("spec.md", 4096)]).attachments;
    expect(sent).toHaveLength(1);
    expect(sent?.[0]).not.toHaveProperty("size");
  });

  it("refuses more files than one request may carry", () => {
    const many = Array.from({ length: MAX_ATTACHMENTS + 1 }, (_, at) => staged(`file-${at}.txt`));
    expect(() => buildCreateRequest(state, "Read them", many)).toThrow(RangeError);
    expect(() => buildCreateRequest(state, "Read them", many)).toThrow(
      `Attach at most ${MAX_ATTACHMENTS} files to one workspace.`,
    );
  });

  it("accepts exactly the cap", () => {
    const full = Array.from({ length: MAX_ATTACHMENTS }, (_, at) => staged(`file-${at}.txt`));
    expect(buildCreateRequest(state, "Read them", full).attachments).toHaveLength(MAX_ATTACHMENTS);
  });

  it("refuses an over-size file by name, so the reader knows which one", () => {
    expect(() =>
      buildCreateRequest(state, "Read it", [
        staged("notes.md"),
        staged("core.dump", MAX_ATTACHMENT_BYTES + 1),
      ]),
    ).toThrow("core.dump is larger than the 32 MB per-file attachment limit.");
  });

  it("accepts a file sitting exactly on the per-file ceiling", () => {
    expect(
      buildCreateRequest(state, "Read it", [staged("big.log", MAX_ATTACHMENT_BYTES)]).attachments,
    ).toHaveLength(1);
  });
});
