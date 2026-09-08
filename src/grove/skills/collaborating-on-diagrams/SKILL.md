---
name: collaborating-on-diagrams
description: Use when working with an existing .drawio diagram through Grove collaboration. Triggers on opening, reading, editing, updating, stopping, or recovering a diagram in a Grove workspace. Covers revisioned managed writes, XML-preserving edits, collaboration state, conflicts, read-only stop, and container/editor limits.
---

# Collaborating on diagrams

Use Grove's diagram tools for a human-and-agent edit of one existing `.drawio`
file in a workspace. The file remains the source of truth. Grove does not create
new diagrams, commit changes, publish diagrams, or share them publicly.

This skill is for managed collaboration, not ordinary source editing. Before a
managed session exists, use your normal editor or shell. Once it is active, use
the diagram tools for every read and write to that file.

## Start with the existing file

Open an existing workspace-relative `.drawio` path. Do not pass an absolute path,
a parent traversal, a symlink, or another file type.

```text
grove_open_diagram(workspace_id="WORKSPACE_ID", path="architecture.drawio")
```

The open result contains the current XML and its revision. It creates a
collaboration identity for the workspace and is idempotent only for the same
active path. Its `diagram.session_id` is the identity for later updates and its
`diagram.mode` is `active`. Opening another path while one is active is a
conflict: finish the first session with `grove_stop_diagram`, then open the other
file.

Read the document through Grove before proposing an edit:

```text
grove_read_diagram(workspace_id="WORKSPACE_ID")
```

Keep the revision returned by the latest successful open, read, or update. Keep
the session identity at `diagram.session_id` as well. Inspect `diagram.mode`
before each edit; it is either `active` or `read_only`. Do not invent an identity
or reuse one after a stop and reopen.

## Preserve draw.io XML

A `.drawio` file is XML. Managed saves use editable, uncompressed `<mxfile>` XML.
Do not base64-encode, wrap, flatten, or replace it with an image format.

- Preserve all `<diagram>` pages, including pages unrelated to the request.
- Preserve existing `mxCell` IDs and references. Add stable new IDs only when a
  new cell genuinely needs one.
- Escape XML values correctly, especially `&`, `<`, `>`, quotes, and label
  content. Put literal markup in a value only when draw.io's existing shape
  markup requires it.
- Keep the document well-formed. Do not introduce DTDs or entities.
- Make the smallest requested structural change. Do not reformat an unrelated
  diagram or regenerate its whole model merely because it is XML.

Use the XML returned by Grove as the edit base. A file that was previously stored
in draw.io's compressed representation may be normalized at this boundary; do not
try to restore compression after a managed save.

## Make a conditional update

Send the whole revised XML with both the collaboration identity and the revision
that you read:

```text
grove_update_diagram(
  workspace_id="WORKSPACE_ID",
  session_id="DIAGRAM_SESSION_ID",
  expected_revision="REVISION_FROM_LATEST_READ",
  xml="<mxfile ...>...</mxfile>",
)
```

A successful update returns the XML and a new revision. Replace your saved base
revision with that result before making the next change. Read again when there is
any doubt about state or content.

A conflict means the document, revision, identity, path, or editability changed.
Never force an update and never retry the identical stale request. Instead:

1. Call `grove_read_diagram(workspace_id="WORKSPACE_ID")` and inspect its
   `diagram` state.
2. If it is still active, reapply the intended minimal change to the newly read
   XML, preserving the other writer's accepted change.
3. Send one new update using the newly returned revision and the current identity.
4. If it is read-only, stop editing and report that the human must explicitly
   open a new active session before collaboration resumes.

This is compare-and-swap, not a merge service. Two writes from one revision cannot
both succeed.

## Inspect the browser-rendered preview

After a managed update succeeds, wait for an open browser Diagram tab to save and
render that acknowledged revision. Then request:

```text
grove_read_diagram_preview(workspace_id="WORKSPACE_ID")
```

The result includes a native PNG image and attachment metadata. In a local CLI
workflow, use `grove diagram preview` and read the returned image path. Help is
available as `grove skills show collaborating-on-diagrams`,
`grove_get_skill(name="collaborating-on-diagrams")`, or
`grove://skills/collaborating-on-diagrams`.

The loop is inspect, correct minimally, and repeat:

1. Read the PNG for the **first page only** of the tagged revision.
2. Check visible labels for clipping, alignment, arrow direction, overlap, and
   other presentation defects relevant to the requested change.
3. If needed, read the current XML again, make the smallest correction, conditionally
   update it, wait for a new browser preview, and inspect again.

The browser creates previews best effort from the already-open draw.io frame. A
missing, pending, or stale preview is actionable: keep or open the Diagram tab,
wait for the acknowledged save, then retry. It does not mean an older image is
current. Browser access is required; there is no server-side renderer in this path.

Do not claim that a PNG proves diagram correctness. Grove neither judges the image
nor covers pages after the first. Inspect later pages manually or with an external
renderer when they matter.

## What other readers can see

Managed reads return acknowledged saves only. A human's in-progress text field,
an offline browser draft, or a label edit draw.io has not yet saved is not a
committed change and ordinary reads cannot see it.

The revision guard coordinates cooperating Grove tools and the visible editor.
It cannot make arbitrary shell writes transactional. Direct edits can still change
the file outside the managed session; Grove detects changed bytes when it reads
or updates, and a stale managed write must conflict rather than overwrite them.
Use managed reads and updates while collaboration is active.

## Stop collaboration deliberately

Stopping is conditional too:

```text
grove_stop_diagram(
  workspace_id="WORKSPACE_ID",
  session_id="DIAGRAM_SESSION_ID",
  expected_revision="LATEST_REVISION",
)
```

After a successful stop, the diagram remains available to read but is read-only.
The same stopped identity may be stopped again; a late write or an identity from a
newer session must not be accepted. Reopening later gives a new identity, so begin
again with a fresh read before editing.

A browser may still hold an unacknowledged draft if a human stopped externally or
lost network access. Do not claim that a stop flushed it. The editor preserves
that draft for explicit conflict recovery rather than silently overwriting or
discarding it.

## Runtime and editor boundaries

The Diagram tab can use the hosted draw.io editor or an operator's compatible
self-hosted editor. Hosted editing runs third-party JavaScript, so apply your
organization's data policy before loading a sensitive diagram. Deployments may
set `NEXT_PUBLIC_GROVE_DRAWIO_URL` to a sanitized self-hosted HTTP(S) editor URL;
this is a deployment setting, not a per-tool argument. A hosted or self-hosted
editor needs network reachability from the browser. Grove does not proxy the
editor or put XML, workspace paths, or credentials in its URL.

A container agent can collaborate only when that container already has a
reachable, configured Grove MCP server or an installed Grove CLI. Do not add a
new network bus, expose the daemon, or mount host credentials merely to make the
tools appear. If neither route exists, ask an outside orchestrator to perform the
managed operation.

The official draw.io MCP project demonstrates possible draw.io file operations,
but Grove does not depend on it. Use Grove's own diagram lifecycle tools here.

## Before you hand off

- Read the diagram once more and confirm the intended XML is acknowledged.
- Confirm the collaboration mode: active if the human should continue, read-only
  if it was explicitly stopped.
- State any conflict, unavailable worktree, missing file, or unsaved human draft
  plainly. Do not treat it as a successful edit.
- Leave Git decisions to the repository workflow. No diagram action creates a
  commit, opens a public link, or publishes this skill.
