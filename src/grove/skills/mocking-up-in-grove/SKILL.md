---
name: mocking-up-in-grove
description: Use when a visual or frontend task in a Grove workspace needs a mockup approved before code — a new control, a redesigned surface, a layout change. Covers drawing a draw.io mockup through Grove's diagram tools, previewing it in the browser, getting sign-off, filing the approved image on the tracker issue, and handing the issue to implementation. For the raw diagram tool contract use collaborating-on-diagrams.
---

# Mocking up in Grove

A frontend change is approved as a picture before it exists as code. The
picture lives in a `.drawio` file the human can open in the workspace's
Diagram tab; the approved render lives on the tracker issue, where people who
never open the workspace can see what was agreed. Nothing in the mockup path
is committed.

## 1. Read before you draw

Read the surface's own guide and its visual contract (`webapp/CLAUDE.md` and
`webapp/design-system.md` for the web app, `src/grove/tui/CLAUDE.md` and
`docs/design-system.md` for the TUI). Take the census of vendored components
before deciding anything is missing. Screenshot the current surface with the
browser tools so the mockup can be compared against something real. Report
`scoping`, then `planning`, through the phase channel `working-in-grove`
describes.

## 2. Draw where nothing tracks it

Write uncompressed `<mxfile>` XML to a workspace-relative path under
`.grove/attachments/` — that directory is excluded from git — with the
`.drawio` suffix the diagram tools require. Then open it managed:

```text
grove_open_diagram(workspace_id=…, path=".grove/attachments/<topic>.drawio")
```

From here every edit goes through `grove_update_diagram` with the revision and
session id from the last read, exactly as `collaborating-on-diagrams` says.
Three drawing rules keep the export honest: plain-text labels (`text;` style,
no `html=1`, `whiteSpace=wrap` so a box's geometry is respected), one shape per
control with its label riding the shape, and brand marks as `shape=image`
cells pointing at Iconify SVG URLs so the mockup uses the marks the code will.
Prefer a small set of real states — resting, open, light, dark — over a wall of
variants.

## 3. Preview, then ask

The human opens the workspace's Diagram tab; that browser frame is the only
renderer. Wait for it, then:

```text
grove_read_diagram_preview(workspace_id=…)
```

Inspect the PNG for clipped labels, overlaps and missing images before showing
it. Present the mockup with what it decides and what it deliberately leaves
out, and stop. Approval is a gate, not a formality: nothing below happens until
the human says yes, and a "yes, but" restarts step 2.

## 4. File the approval

Create the tracker issue with the repository's real labels, then attach the
rendered PNG to it and reference the asset from the body — an issue whose
design lives only in a chat transcript loses it. On Gitea the upload is
`POST /repos/{owner}/{repo}/issues/{n}/assets` as multipart, and the asset's
`browser_download_url` is what the body's image embeds. Attach the raw
`.drawio` too only when the tracker accepts it; the PNG is the record either
way. Then attach the issue to the workspace (`grove tickets attach <n>` or
`grove_attach_ticket`) so the phase channel starts publishing against it, and
stop the diagram session with `grove_stop_diagram` — the file stays readable,
and a stale collaboration is one more thing to conflict with later.

## 5. Build from the picture

Implement against the approved render, not against memory of the conversation.
When the built surface exists, screenshot it beside the mockup and put the
pair on the issue: the comparison is the review. Fold whatever the mockup
taught the visual contract into `design-system.md`, never into a code comment.
The PR that follows names the issue on the tracker, not in its own body.
